"""Hardened loader for the PowerDNS runtime compatibility adapter."""
from __future__ import annotations

import importlib.util
import os
import pathlib
import secrets
import stat
import sys

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


def write_atomic_preserving(path: pathlib.Path, text: str) -> None:
    metadata = path.lstat()
    mode = stat.S_IMODE(metadata.st_mode)
    fd, temporary = _open_temporary(path, mode)
    active_error: BaseException | None = None
    cleanup_error: BaseException | None = None
    try:
        payload = text.encode('utf-8')
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise BuildError(f'could not write {temporary}')
            view = view[written:]
        os.fchown(fd, metadata.st_uid, metadata.st_gid)
        os.fchmod(fd, mode)
        _copy_xattrs(path, temporary)
        os.fsync(fd)
        descriptor, fd = fd, -1
        os.close(descriptor)
        os.replace(temporary, path)
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


for _name in dir(_IMPL):
    if _name not in {
        'applied_dns_mode', '_copy_xattrs', 'write_atomic_preserving'
    } and _name not in globals():
        globals()[_name] = getattr(_IMPL, _name)

_IMPL.applied_dns_mode = applied_dns_mode
_IMPL._copy_xattrs = _copy_xattrs
_IMPL.write_atomic_preserving = write_atomic_preserving

install = _IMPL.install
reconcile_dns_services = _IMPL.reconcile_dns_services
guarded_apply_build = _IMPL.guarded_apply_build
