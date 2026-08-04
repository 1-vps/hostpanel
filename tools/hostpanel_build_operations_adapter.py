"""Fail-closed runtime hardening for shared CustomBuild operations."""
from __future__ import annotations

import os
import pathlib
import secrets
import stat

import hostpanel_build_operations as operations
from hostpanel_build_config import BuildError

_PRESERVE_CURRENT_XATTRS = object()


def _command_text(value: object) -> str:
    if value is None:
        return ''
    if isinstance(value, bytes):
        return value.decode('utf-8', errors='replace').strip()
    return str(value).strip()


def service_active(name: str) -> bool:
    completed = operations.run_command(
        ['systemctl', 'is-active', name], check=False, capture=True
    )
    state = _command_text(getattr(completed, 'stdout', '')).lower()
    error = _command_text(getattr(completed, 'stderr', ''))
    if completed.returncode == 0 and state == 'active' and not error:
        return True
    if (
        completed.returncode in {3, 4}
        and state in {'inactive', 'failed', 'unknown', 'not-found'}
        and not error
    ):
        return False
    detail = error or state or str(completed.returncode)
    raise BuildError(f'could not determine active state for {name}: {detail}')


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
        raise BuildError(f'could not capture configuration metadata for {path}: {exc}') from exc


def _apply_xattrs(path: pathlib.Path, values: dict[str, bytes]) -> None:
    try:
        existing = set(os.listxattr(path, follow_symlinks=False))
        desired = set(values)
        for name in existing - desired:
            os.removexattr(path, name, follow_symlinks=False)
        for name, value in values.items():
            os.setxattr(path, name, value, follow_symlinks=False)
    except OSError as exc:
        raise BuildError(f'could not restore configuration metadata for {path}: {exc}') from exc


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


def write_atomic_root(path: pathlib.Path, text: str, mode: int = 0o644) -> None:
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

    desired_xattrs: dict[str, bytes] | None = None
    if os.path.lexists(path):
        metadata = path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) & 0o022
        ):
            raise BuildError(f'unsafe configuration file: {path}')
        desired_xattrs = _capture_xattrs(path)

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
        os.fchown(fd, 0, 0)
        os.fchmod(fd, mode)
        if desired_xattrs is not None:
            _apply_xattrs(temporary, desired_xattrs)
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


def mask_service(name: str, log_path: pathlib.Path) -> bool:
    was_active = service_active(name)
    if was_active:
        operations.run_command(
            ['systemctl', 'stop', name], log_path=log_path
        )
    try:
        operations.run_command(
            ['systemctl', 'mask', '--runtime', name], log_path=log_path
        )
    except BaseException as original_error:
        if was_active:
            try:
                operations.run_command(
                    ['systemctl', 'start', name], log_path=log_path
                )
            except BaseException as rollback_error:
                raise BuildError(
                    f'could not mask {name}; restart rollback also failed: '
                    f'{rollback_error}'
                ) from original_error
        raise
    return was_active


def install() -> None:
    operations.service_active = service_active
    operations.write_atomic_root = write_atomic_root
    operations.mask_service = mask_service


__all__ = ['install', 'mask_service', 'service_active', 'write_atomic_root']
