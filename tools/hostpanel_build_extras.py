"""Hardened loader for optional CustomBuild extras."""
from __future__ import annotations

import importlib.util
import os
import pathlib
import secrets
import shutil
import stat
import sys
import types

_IMPL_PATH = pathlib.Path(__file__).with_name('hostpanel_build_extras_impl.py')
_SPEC = importlib.util.spec_from_file_location(
    '_hostpanel_build_extras_impl', _IMPL_PATH
)
if _SPEC is None or _SPEC.loader is None:
    raise ImportError(f'cannot load extras implementation: {_IMPL_PATH}')
_IMPL = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _IMPL
_SPEC.loader.exec_module(_IMPL)

BuildError = _IMPL.BuildError
VARNISH_MODE_FILE = _IMPL.VARNISH_MODE_FILE
_PRESERVE_CURRENT_XATTRS = object()


def _trusted_varnish_mode(default: str = 'off') -> str:
    path = VARNISH_MODE_FILE
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return default
    except OSError as exc:
        raise BuildError(f'cannot inspect Varnish runtime mode: {path}') from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise BuildError(f'unsafe Varnish runtime mode file: {path}')
    try:
        mode = path.read_text(encoding='ascii').strip()
    except (OSError, UnicodeError) as exc:
        raise BuildError(f'cannot read Varnish runtime mode: {path}') from exc
    if mode not in {'off', 'on'}:
        raise BuildError('invalid Varnish runtime mode')
    return mode


def _capture_xattrs(path: pathlib.Path) -> dict[str, bytes]:
    required = ('listxattr', 'getxattr', 'setxattr', 'removexattr')
    if not all(hasattr(os, name) for name in required):
        raise BuildError('extended-attribute support is unavailable')
    try:
        return {
            name: os.getxattr(path, name, follow_symlinks=False)
            for name in os.listxattr(path, follow_symlinks=False)
        }
    except OSError as exc:
        raise BuildError(f'could not capture extended metadata for {path}: {exc}') from exc


def _apply_xattrs(path: pathlib.Path, values: dict[str, bytes]) -> None:
    try:
        existing = set(os.listxattr(path, follow_symlinks=False))
        desired = set(values)
        for name in existing - desired:
            os.removexattr(path, name, follow_symlinks=False)
        for name, value in values.items():
            os.setxattr(path, name, value, follow_symlinks=False)
    except OSError as exc:
        raise BuildError(f'could not restore extended metadata for {path}: {exc}') from exc


def _open_temporary(path: pathlib.Path, mode: int) -> tuple[int, pathlib.Path]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    for _ in range(32):
        temporary = path.with_name(
            f'.{path.name}.hostpanel-build.{secrets.token_hex(12)}'
        )
        try:
            return os.open(temporary, flags, mode), temporary
        except FileExistsError:
            continue
    raise BuildError(f'could not allocate temporary file for {path}')


def write_atomic_bytes(
    path: pathlib.Path, payload: bytes, mode: int = 0o644,
    uid: int = 0, gid: int = 0, *,
    xattrs: dict[str, bytes] | object = _PRESERVE_CURRENT_XATTRS,
) -> None:
    _IMPL.require_root()
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

    desired_xattrs: dict[str, bytes] | None
    if xattrs is _PRESERVE_CURRENT_XATTRS:
        desired_xattrs = _capture_xattrs(path) if exists else None
    elif isinstance(xattrs, dict):
        desired_xattrs = dict(xattrs)
    else:
        raise BuildError('invalid extended-metadata snapshot')

    fd, temporary = _open_temporary(path, mode)
    active_error: BaseException | None = None
    cleanup_error: BaseException | None = None
    try:
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise BuildError(f'could not write {temporary}')
            view = view[written:]
        os.fsync(fd)
        os.fchown(fd, uid, gid)
        os.fchmod(fd, mode)
        if desired_xattrs is not None:
            _apply_xattrs(temporary, desired_xattrs)
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


def write_atomic_text(path: pathlib.Path, text: str, mode: int = 0o644) -> None:
    uid = 0
    gid = 0
    if os.path.lexists(path):
        metadata = path.lstat()
        uid = metadata.st_uid
        gid = metadata.st_gid
    write_atomic_bytes(path, text.encode('utf-8'), mode, uid, gid)


def validate_varnish(options: dict[str, str], log_path: pathlib.Path) -> None:
    origin_port = _IMPL.varnish_origin_port(options) \
        if options.get('varnish') == 'on' else (
            8088 if options.get('webserver') == 'openlitespeed' else 8080
        )
    mode = _trusted_varnish_mode()
    if mode != options.get('varnish'):
        raise BuildError('Varnish runtime mode does not match build.conf')
    if mode == 'off':
        active = _IMPL.run_command(
            ['systemctl', 'is-active', '--quiet', 'varnish.service'],
            check=False, capture=True,
        )
        if active.returncode == 0:
            raise BuildError('Varnish service is active while varnish=off')
        for path in _IMPL.managed_proxy_files():
            if (
                f'proxy_pass http://127.0.0.1:{_IMPL.VARNISH_PORT};'
                in path.read_text(encoding='utf-8')
            ):
                raise BuildError(
                    f'nginx vhost still points to disabled Varnish: {path}'
                )
        return
    binary = shutil.which('varnishd')
    if binary is None or not _IMPL.VARNISH_VCL.is_file():
        raise BuildError('Varnish runtime is missing')
    _IMPL.run_command(
        [binary, '-C', '-f', str(_IMPL.VARNISH_VCL)], log_path=log_path
    )
    _IMPL.run_command(
        ['systemctl', 'is-active', '--quiet', 'varnish.service'],
        log_path=log_path,
    )
    if not _IMPL.loopback_listener(_IMPL.VARNISH_PORT):
        raise BuildError('Varnish port 6081 is not loopback-only')
    direct = f'proxy_pass http://127.0.0.1:{origin_port};'
    cached = f'proxy_pass http://127.0.0.1:{_IMPL.VARNISH_PORT};'
    for path in _IMPL.managed_proxy_files():
        text = path.read_text(encoding='utf-8')
        if cached not in text or direct in text:
            raise BuildError(f'nginx vhost is not routed through Varnish: {path}')


for _name in dir(_IMPL):
    if _name not in {
        'validate_varnish', 'write_atomic_bytes', 'write_atomic_text'
    } and _name not in globals():
        globals()[_name] = getattr(_IMPL, _name)

_IMPL.validate_varnish = validate_varnish
_IMPL.write_atomic_bytes = write_atomic_bytes
_IMPL.write_atomic_text = write_atomic_text


class _ForwardingModule(types.ModuleType):
    def __setattr__(self, name: str, value) -> None:
        super().__setattr__(name, value)
        implementation = super().__getattribute__('_IMPL')
        if hasattr(implementation, name):
            setattr(implementation, name, value)


_module = sys.modules.get(__name__)
if _module is not None:
    _module.__class__ = _ForwardingModule
