#!/usr/bin/env python3
"""Hardened loader for the PowerDNS runtime patcher."""
from __future__ import annotations

import importlib.util
import os
import pathlib
import secrets
import stat
import sys
import types

_IMPL_PATH = pathlib.Path(__file__).with_name('patch_powerdns_runtime_impl.py')
_SPEC = importlib.util.spec_from_file_location(
    '_hostpanel_patch_powerdns_runtime_impl', _IMPL_PATH
)
if _SPEC is None or _SPEC.loader is None:
    raise SystemExit(f'cannot load PowerDNS runtime patcher: {_IMPL_PATH}')
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
            f'.{path.name}.powerdns.{secrets.token_hex(12)}'
        )
        try:
            return os.open(temporary, flags, mode), temporary
        except FileExistsError:
            continue
    raise SystemExit(f'could not allocate temporary file for {path}')


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


for _name in dir(_IMPL):
    if _name not in {
        'write_atomic', 'copy_xattrs', 'patch_core', 'patch_root', 'main'
    } and _name not in globals():
        globals()[_name] = getattr(_IMPL, _name)
_IMPL.write_atomic = write_atomic


def _sync_patch_dependencies() -> None:
    _IMPL.trusted_file = globals()['trusted_file']
    _IMPL.write_atomic = write_atomic


def patch_core(path: pathlib.Path) -> None:
    _sync_patch_dependencies()
    _IMPL.patch_core(path)


def patch_root(path: pathlib.Path) -> None:
    _sync_patch_dependencies()
    _IMPL.patch_root(path)


def _patched_core_text(text: str) -> str:
    return _IMPL.replace_once(
        text, _IMPL.CORE_OLD, _IMPL.CORE_NEW, 'core DNS service'
    )


def _patched_root_text(text: str) -> str:
    text = _IMPL.replace_once(
        text, _IMPL.ROOT_ANCHOR, _IMPL.ROOT_MODE, 'root DNS mode'
    )
    if _IMPL.ALLOW_NEW not in text:
        if text.count(_IMPL.ALLOW_OLD) != 1:
            raise SystemExit('unexpected service allowlist shape')
        text = text.replace(_IMPL.ALLOW_OLD, _IMPL.ALLOW_NEW, 1)
    return _IMPL.replace_once(
        text, _IMPL.DNSSEC_OLD, _IMPL.DNSSEC_NEW,
        'PowerDNS DNSSEC guard',
    )


def _content_matches(path: pathlib.Path, expected: str) -> bool:
    try:
        return path.read_text(encoding='utf-8') == expected
    except (OSError, UnicodeError):
        return False


def _rollback_runtime_files(
    core_path: pathlib.Path,
    core_original: str,
    core_metadata: os.stat_result,
    root_path: pathlib.Path,
    root_original: str,
    root_metadata: os.stat_result,
    original_error: BaseException,
) -> None:
    root_needs_restore = not _content_matches(root_path, root_original)
    core_needs_restore = not _content_matches(core_path, core_original)
    errors: list[str] = []
    root_restored = True

    if root_needs_restore:
        try:
            write_atomic(root_path, root_original, root_metadata)
        except BaseException as exc:
            root_restored = False
            errors.append(f'root-helper rollback failed: {exc}')

    if core_needs_restore:
        if root_needs_restore and not root_restored:
            errors.append(
                'core rollback skipped because root-helper rollback failed'
            )
        else:
            try:
                write_atomic(core_path, core_original, core_metadata)
            except BaseException as exc:
                errors.append(f'core rollback failed: {exc}')

    if errors:
        raise SystemExit(
            'PowerDNS runtime patch failed and rollback also failed: '
            + '; '.join(errors)
        ) from original_error


def main() -> int:
    if os.geteuid() != 0:
        raise SystemExit('patch_powerdns_runtime.py must run as root')

    core_path = APP_ROOT / 'core.py'
    root_path = APP_ROOT / 'hostpanel-root'
    core_metadata = trusted_file(core_path)
    root_metadata = trusted_file(root_path)
    core_original = core_path.read_text(encoding='utf-8')
    root_original = root_path.read_text(encoding='utf-8')

    core_updated = _patched_core_text(core_original)
    root_updated = _patched_root_text(root_original)

    try:
        write_atomic(core_path, core_updated, core_metadata)
        write_atomic(root_path, root_updated, root_metadata)
    except BaseException as original_error:
        _rollback_runtime_files(
            core_path,
            core_original,
            core_metadata,
            root_path,
            root_original,
            root_metadata,
            original_error,
        )
        raise
    return 0


class _ForwardingModule(types.ModuleType):
    def __setattr__(self, name: str, value) -> None:
        super().__setattr__(name, value)
        implementation = super().__getattribute__('_IMPL')
        if hasattr(implementation, name):
            setattr(implementation, name, value)


_module = sys.modules.get(__name__)
if _module is not None:
    _module.__class__ = _ForwardingModule

if __name__ == '__main__':
    raise SystemExit(main())
