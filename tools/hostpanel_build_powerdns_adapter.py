"""Hardened loader for the PowerDNS runtime compatibility adapter."""
from __future__ import annotations

import ctypes
import ctypes.util
import importlib.util
import os
import pathlib
import secrets
import stat
import sys
import types

_IMPL_PATH = pathlib.Path(__file__).with_name(
    'hostpanel_build_powerdns_adapter_impl.py'
)
_SPEC = importlib.util.spec_from_file_location(
    '_hostpanel_build_powerdns_adapter_impl', _IMPL_PATH
)
if _SPEC is None or _SPEC.loader is None:
    raise ImportError(f'cannot load PowerDNS adapter implementation: {_IMPL_PATH}')
_IMPL = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _IMPL
_SPEC.loader.exec_module(_IMPL)

BuildError = _IMPL.BuildError
operations = _IMPL.operations
_BASE_INSTALL = _IMPL.install
_CORE_CONFIGURE_POWERDNS = operations.configure_powerdns


def applied_dns_mode() -> str:
    path = operations.DNS_MODE_FILE
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return 'bind'
    except OSError as exc:
        raise BuildError(f'cannot inspect applied HostPanel DNS mode: {path}') from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise BuildError(f'unsafe applied HostPanel DNS mode file: {path}')
    try:
        mode = path.read_text(encoding='ascii').strip()
    except (OSError, UnicodeError) as exc:
        raise BuildError(f'cannot read applied HostPanel DNS mode: {path}') from exc
    if mode not in {'bind', 'powerdns'}:
        raise BuildError('invalid applied HostPanel DNS mode')
    return mode


def service_active(name: str) -> bool:
    completed = _IMPL.run_command(
        ['systemctl', 'is-active', name], check=False, capture=True
    )
    state = _IMPL._stdout_state(completed)
    error = _IMPL._query_error(completed)
    if completed.returncode == 0 and state == 'active' and not error:
        return True
    if (
        completed.returncode in {3, 4}
        and state in {'inactive', 'failed', 'unknown', 'not-found'}
        and not error
    ):
        return False
    raise BuildError(
        f'could not determine active state for {name}: '
        f'{error or state or completed.returncode}'
    )


def service_enabled(name: str) -> bool:
    completed = _IMPL.run_command(
        ['systemctl', 'is-enabled', name], check=False, capture=True
    )
    state = _IMPL._stdout_state(completed)
    error = _IMPL._query_error(completed)
    if completed.returncode == 0 and state == 'enabled' and not error:
        return True
    if (
        state in {'masked', 'masked-runtime'}
        and completed.returncode in {0, 1, 3, 4}
        and not error
    ):
        raise BuildError(
            f'{name} is {state}; refusing to remove an administrative mask'
        )
    disabled_states = {
        'disabled', 'static', 'indirect', 'generated', 'transient',
        'alias', 'not-found',
    }
    if (
        state in disabled_states
        and completed.returncode in {0, 1, 3, 4}
        and not error
    ):
        return False
    raise BuildError(
        f'could not determine enabled state for {name}: '
        f'{error or state or completed.returncode}'
    )


def _copy_xattrs(source: pathlib.Path, destination: pathlib.Path) -> None:
    required = ('listxattr', 'getxattr', 'setxattr', 'removexattr')
    if not all(hasattr(os, name) for name in required):
        raise BuildError('extended-attribute support is unavailable')
    try:
        source_names = set(os.listxattr(source, follow_symlinks=False))
        destination_names = set(os.listxattr(destination, follow_symlinks=False))
        for name in destination_names - source_names:
            os.removexattr(destination, name, follow_symlinks=False)
        for name in source_names:
            value = os.getxattr(source, name, follow_symlinks=False)
            os.setxattr(destination, name, value, follow_symlinks=False)
    except OSError as exc:
        raise BuildError(
            f'could not preserve extended metadata for {source}: {exc}'
        ) from exc


def _apply_expected_selinux_context(
    fd: int, path: pathlib.Path, mode: int
) -> None:
    """Apply the policy context for the final pathname to a new temporary inode."""
    library_name = ctypes.util.find_library('selinux')
    selinuxfs = pathlib.Path('/sys/fs/selinux/enforce')
    if not library_name:
        if selinuxfs.exists():
            raise BuildError(
                'SELinux is enabled but libselinux could not be loaded'
            )
        return
    try:
        library = ctypes.CDLL(library_name, use_errno=True)
    except OSError as exc:
        if selinuxfs.exists():
            raise BuildError('could not load libselinux') from exc
        return

    library.is_selinux_enabled.argtypes = []
    library.is_selinux_enabled.restype = ctypes.c_int
    enabled = library.is_selinux_enabled()
    if enabled < 0:
        raise BuildError('could not determine whether SELinux is enabled')
    if enabled == 0:
        return

    library.matchpathcon.argtypes = [
        ctypes.c_char_p,
        ctypes.c_uint,
        ctypes.POINTER(ctypes.c_char_p),
    ]
    library.matchpathcon.restype = ctypes.c_int
    library.fsetfilecon.argtypes = [ctypes.c_int, ctypes.c_char_p]
    library.fsetfilecon.restype = ctypes.c_int
    library.freecon.argtypes = [ctypes.c_void_p]
    library.freecon.restype = None

    context = ctypes.c_char_p()
    expected_mode = stat.S_IFREG | stat.S_IMODE(mode)
    if library.matchpathcon(
        os.fsencode(path), expected_mode, ctypes.byref(context)
    ) != 0:
        error = ctypes.get_errno()
        raise BuildError(
            f'could not determine SELinux context for {path}: '
            f'{os.strerror(error)}'
        )
    try:
        if not context.value:
            raise BuildError(f'empty SELinux context for {path}')
        if library.fsetfilecon(fd, context) != 0:
            error = ctypes.get_errno()
            raise BuildError(
                f'could not apply SELinux context for {path}: '
                f'{os.strerror(error)}'
            )
    finally:
        library.freecon(ctypes.cast(context, ctypes.c_void_p))


def _open_temporary(path: pathlib.Path, mode: int) -> tuple[int, pathlib.Path]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    for _ in range(32):
        temporary = path.with_name(
            f'.{path.name}.hostpanel-pdns.{secrets.token_hex(12)}'
        )
        try:
            return os.open(temporary, flags, mode), temporary
        except FileExistsError:
            continue
    raise BuildError(f'could not allocate temporary file for {path}')


def _fsync_parent(path: pathlib.Path) -> None:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, 'O_DIRECTORY'):
        flags |= os.O_DIRECTORY
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    directory_fd = os.open(path.parent, flags)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _write_payload(
    path: pathlib.Path, text: str, mode: int, uid: int, gid: int,
    *, preserve_xattrs: bool,
) -> None:
    fd, temporary = _open_temporary(path, mode)
    active_error: BaseException | None = None
    cleanup_error: BaseException | None = None
    try:
        view = memoryview(text.encode('utf-8'))
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise BuildError(f'could not write {temporary}')
            view = view[written:]
        os.fsync(fd)
        os.fchown(fd, uid, gid)
        os.fchmod(fd, mode)
        if preserve_xattrs:
            _copy_xattrs(path, temporary)
        else:
            _apply_expected_selinux_context(fd, path, mode)
        os.fsync(fd)
        descriptor, fd = fd, -1
        os.close(descriptor)
        os.replace(temporary, path)
        _fsync_parent(path)
    except BaseException as exc:
        active_error = exc
        raise
    finally:
        if fd >= 0:
            descriptor, fd = fd, -1
            try:
                os.close(descriptor)
            except BaseException as exc:
                cleanup_error = exc
        try:
            temporary.unlink(missing_ok=True)
        except BaseException as exc:
            if cleanup_error is None:
                cleanup_error = exc
        if active_error is None and cleanup_error is not None:
            raise cleanup_error


def write_atomic_preserving(path: pathlib.Path, text: str) -> None:
    metadata = path.lstat()
    _write_payload(
        path, text, stat.S_IMODE(metadata.st_mode),
        metadata.st_uid, metadata.st_gid, preserve_xattrs=True,
    )


def write_atomic_root(
    path: pathlib.Path, text: str, mode: int = 0o644
) -> None:
    operations.require_root()
    path.parent.mkdir(parents=True, mode=0o755, exist_ok=True)
    parent = path.parent.lstat()
    if (
        not stat.S_ISDIR(parent.st_mode)
        or stat.S_ISLNK(parent.st_mode)
        or parent.st_uid != 0
        or stat.S_IMODE(parent.st_mode) & 0o022
    ):
        raise BuildError(f'unsafe configuration directory: {path.parent}')
    exists = os.path.lexists(path)
    if exists:
        metadata = path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) & 0o022
        ):
            raise BuildError(f'unsafe configuration file: {path}')
    _write_payload(
        path, text, mode, 0, 0, preserve_xattrs=exists
    )


def select_powerdns_backend_config(
    native: pathlib.Path, include_dir: pathlib.Path, default: pathlib.Path
) -> pathlib.Path:
    trusted_root_file(native)
    trusted_root_directory_chain(include_dir)
    native_backends = launch_values(native.read_text(encoding='utf-8'))
    if native_backends:
        raise BuildError(
            'PowerDNS native configuration has an unmanaged backend: '
            + ', '.join(sorted(native_backends))
        )
    candidates: list[pathlib.Path] = []
    for path in sorted(include_dir.glob('*.conf')):
        trusted_root_file(path)
        text = path.read_text(encoding='utf-8')
        backends = launch_values(text)
        if not backends:
            continue
        if backends != {'bind'}:
            raise BuildError(
                'PowerDNS has an unmanaged backend configuration: '
                + ', '.join(sorted(backends))
            )
        if operations.PDNS_MANAGED_MARKER not in text:
            keys = active_setting_keys(text)
            if path.name != 'bind.conf' or not keys <= {'launch', 'bind-config'}:
                raise BuildError(
                    'PowerDNS BIND backend configuration is not a safe '
                    f'package default: {path}'
                )
        candidates.append(path)
    if len(candidates) > 1:
        raise BuildError('PowerDNS has multiple BIND backend configurations')
    return candidates[0] if candidates else default


def _configure_powerdns_readable(
    platform, log_path: pathlib.Path
) -> None:
    managed_conf, zone_dir, dns_group, _ = operations.dns_layout(platform)
    trusted_root_file(managed_conf)
    trusted_managed_zone_directory(zone_dir, dns_group)

    native = operations.native_powerdns_config()
    include_dir = operations.powerdns_include_dir(native)
    target = operations.select_powerdns_backend_config(
        native, include_dir, include_dir / operations.PDNS_DROPIN_NAME
    )
    trusted_root_directory_chain(include_dir)
    directory_mode = stat.S_IMODE(include_dir.lstat().st_mode)
    if directory_mode & 0o055 != 0o055:
        raise BuildError(
            f'PowerDNS include directory is not readable: {include_dir}'
        )

    base_writer = operations.write_atomic_root

    def readable_writer(
        write_path: pathlib.Path, text: str, write_mode: int = 0o644
    ) -> None:
        if write_path == target:
            write_mode = 0o644
        base_writer(write_path, text, write_mode)

    operations.write_atomic_root = readable_writer
    try:
        _CORE_CONFIGURE_POWERDNS(platform, log_path)
    finally:
        operations.write_atomic_root = base_writer

    trusted_root_file(target)
    metadata = target.lstat()
    if metadata.st_gid != 0 or stat.S_IMODE(metadata.st_mode) != 0o644:
        raise BuildError(f'unsafe PowerDNS backend permissions: {target}')


for _name in dir(_IMPL):
    if _name not in {
        'applied_dns_mode', 'service_active', 'service_enabled',
        '_copy_xattrs', 'write_atomic_preserving',
        'select_powerdns_backend_config', 'install',
    } and _name not in globals():
        globals()[_name] = getattr(_IMPL, _name)

_IMPL.applied_dns_mode = applied_dns_mode
_IMPL.service_active = service_active
_IMPL.service_enabled = service_enabled
_IMPL._copy_xattrs = _copy_xattrs
_IMPL.write_atomic_preserving = write_atomic_preserving
_IMPL.select_powerdns_backend_config = select_powerdns_backend_config


def install() -> None:
    _BASE_INSTALL()
    operations.write_atomic_root = write_atomic_root
    operations.select_powerdns_backend_config = select_powerdns_backend_config
    operations.configure_powerdns = _configure_powerdns_readable


reconcile_dns_services = _IMPL.reconcile_dns_services
guarded_apply_build = _IMPL.guarded_apply_build


class _ForwardingModule(types.ModuleType):
    """Keep test/runtime monkeypatches visible inside the preserved implementation."""

    def __setattr__(self, name: str, value) -> None:
        super().__setattr__(name, value)
        implementation = super().__getattribute__('_IMPL')
        if hasattr(implementation, name):
            setattr(implementation, name, value)


_module = sys.modules.get(__name__)
if _module is not None:
    _module.__class__ = _ForwardingModule
