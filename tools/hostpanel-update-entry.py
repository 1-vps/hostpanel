#!/usr/bin/env python3
"""Hardened runtime entrypoint for the signed HostPanel updater."""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import secrets
import stat
import sys

_IMPL_PATH = pathlib.Path(__file__).with_name('hostpanel-update-impl.py')
_SPEC = importlib.util.spec_from_file_location(
    '_hostpanel_update_impl', _IMPL_PATH
)
if _SPEC is None or _SPEC.loader is None:
    raise SystemExit(f'cannot load HostPanel updater implementation: {_IMPL_PATH}')
_IMPL = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _IMPL
_SPEC.loader.exec_module(_IMPL)

UpdateError = _IMPL.UpdateError
_RAW_OS = os
_RAW_OS_WRITE = os.write


def _write_all(fd: int, payload) -> int:
    view = memoryview(payload)
    total = len(view)
    while view:
        written = _RAW_OS_WRITE(fd, view)
        if written <= 0:
            raise UpdateError('could not complete updater file write')
        view = view[written:]
    return total


class _UpdaterOsProxy:
    def __getattr__(self, name: str):
        return getattr(_RAW_OS, name)

    write = staticmethod(_write_all)


_UPDATER_OS = _UpdaterOsProxy()
# The preserved updater uses its module-global `os` reference for release
# downloads, archive extraction, and status publication. Replace only that
# reference; do not mutate the process-global os module used by other code.
_IMPL.os = _UPDATER_OS


def _capture_xattrs(path: pathlib.Path) -> dict[str, bytes]:
    required = ('listxattr', 'getxattr', 'setxattr', 'removexattr')
    if not all(hasattr(os, name) for name in required):
        raise UpdateError('extended-attribute support is unavailable')
    try:
        return {
            name: os.getxattr(path, name, follow_symlinks=False)
            for name in os.listxattr(path, follow_symlinks=False)
        }
    except OSError as exc:
        raise UpdateError(
            f'could not capture update-status metadata for {path}: {exc}'
        ) from exc


def _apply_xattrs(path: pathlib.Path, values: dict[str, bytes]) -> None:
    try:
        existing = set(os.listxattr(path, follow_symlinks=False))
        desired = set(values)
        for name in existing - desired:
            os.removexattr(path, name, follow_symlinks=False)
        for name, value in values.items():
            os.setxattr(path, name, value, follow_symlinks=False)
    except OSError as exc:
        raise UpdateError(
            f'could not restore update-status metadata for {path}: {exc}'
        ) from exc


def _open_temporary(path: pathlib.Path) -> tuple[int, pathlib.Path]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    for _ in range(32):
        temporary = path.with_name(
            f'.{path.name}.hostpanel-update.{secrets.token_hex(12)}'
        )
        try:
            return os.open(temporary, flags, 0o600), temporary
        except FileExistsError:
            continue
    raise UpdateError(f'could not allocate status temporary for {path}')


def _fsync_parent(path: pathlib.Path) -> None:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, 'O_DIRECTORY'):
        flags |= os.O_DIRECTORY
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    try:
        directory_fd = os.open(path.parent, flags)
    except OSError as exc:
        raise UpdateError(f'could not open status directory: {path.parent}') from exc
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _validate_status_target(
    path: pathlib.Path, owner_uid: int, owner_gid: int
) -> dict[str, bytes] | None:
    if not os.path.lexists(path):
        return None
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != owner_uid
        or metadata.st_gid != owner_gid
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise UpdateError(f'unsafe update-status file: {path}')
    return _capture_xattrs(path)


def atomic_json(path: pathlib.Path, payload: dict[str, object]) -> None:
    owner_uid, owner_gid = _IMPL._owner_ids()
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as exc:
        raise UpdateError(
            f'could not create update-status directory: {path.parent}'
        ) from exc
    _IMPL._validate_direct_parent(path, owner_uid, owner_gid)
    existing_xattrs = _validate_status_target(path, owner_uid, owner_gid)
    encoded = (
        json.dumps(payload, sort_keys=True, indent=2) + '\n'
    ).encode('utf-8')

    fd, temporary = _open_temporary(path)
    active_error: BaseException | None = None
    cleanup_error: BaseException | None = None
    try:
        view = memoryview(encoded)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise UpdateError(f'could not write update status: {temporary}')
            view = view[written:]
        os.fsync(fd)
        os.fchown(fd, owner_uid, owner_gid)
        os.fchmod(fd, 0o600)
        if existing_xattrs is not None:
            _apply_xattrs(temporary, existing_xattrs)
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


_IMPL.atomic_json = atomic_json


def main(argv: list[str] | None = None) -> int:
    _IMPL.os = _UPDATER_OS
    _IMPL.atomic_json = atomic_json
    return _IMPL.main(argv)


if __name__ == '__main__':
    raise SystemExit(main())
