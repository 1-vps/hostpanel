#!/usr/bin/env python3
"""Hardened loader for the extras doctor runtime patcher."""
from __future__ import annotations

import importlib.util
import os
import pathlib
import secrets
import stat
import sys
import types

_IMPL_PATH = pathlib.Path(__file__).with_name('patch_extras_doctor_impl.py')
_SPEC = importlib.util.spec_from_file_location(
    '_hostpanel_patch_extras_doctor_impl', _IMPL_PATH
)
if _SPEC is None or _SPEC.loader is None:
    raise SystemExit(f'cannot load extras doctor patcher: {_IMPL_PATH}')
_IMPL = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _IMPL
_SPEC.loader.exec_module(_IMPL)


def copy_xattrs(source: pathlib.Path, destination: pathlib.Path) -> None:
    source_names = set(os.listxattr(source, follow_symlinks=False))
    destination_names = set(os.listxattr(destination, follow_symlinks=False))
    for name in destination_names - source_names:
        os.removexattr(destination, name, follow_symlinks=False)
    for name in source_names:
        os.setxattr(
            destination, name,
            os.getxattr(source, name, follow_symlinks=False),
            follow_symlinks=False,
        )


def _open_temporary(path: pathlib.Path, mode: int) -> tuple[int, pathlib.Path]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    for _ in range(32):
        temporary = path.with_name(
            f'.{path.name}.extras.{secrets.token_hex(12)}'
        )
        try:
            return os.open(temporary, flags, mode), temporary
        except FileExistsError:
            continue
    raise SystemExit(f'could not allocate temporary file for {path}')


def write_atomic(
    path: pathlib.Path, text: str, metadata: os.stat_result
) -> None:
    mode = stat.S_IMODE(metadata.st_mode)
    fd, temporary = _open_temporary(path, mode)
    active_error: BaseException | None = None
    cleanup_error: BaseException | None = None
    try:
        view = memoryview(text.encode('utf-8'))
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise SystemExit(f'could not write {temporary}')
            view = view[written:]
        os.fsync(fd)
        os.fchown(fd, metadata.st_uid, metadata.st_gid)
        os.fchmod(fd, mode)
        copy_xattrs(path, temporary)
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
    if _name not in {'write_atomic', 'copy_xattrs', 'patch'} and _name not in globals():
        globals()[_name] = getattr(_IMPL, _name)
_IMPL.write_atomic = write_atomic


def patch(path: pathlib.Path = _IMPL.TARGET) -> None:
    _IMPL.trusted_file = globals()['trusted_file']
    _IMPL.write_atomic = write_atomic
    _IMPL.patch(path)


class _ForwardingModule(types.ModuleType):
    def __setattr__(self, name: str, value) -> None:
        super().__setattr__(name, value)
        implementation = super().__getattribute__('_IMPL')
        if hasattr(implementation, name):
            setattr(implementation, name, value)


_module = sys.modules.get(__name__)
if _module is not None:
    _module.__class__ = _ForwardingModule
main = _IMPL.main

if __name__ == '__main__':
    raise SystemExit(main())