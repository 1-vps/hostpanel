#!/usr/bin/env python3
"""Transactional public entrypoint for HostPanel webserver reconciliation."""
from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import importlib.util
import os
import pathlib
import stat
import sys
import types

_IMPL_PATH = pathlib.Path(__file__).with_name('hostpanel_build_web_impl.py')
_SPEC = importlib.util.spec_from_file_location(
    '_hostpanel_build_web_impl', _IMPL_PATH
)
if _SPEC is None or _SPEC.loader is None:
    raise ImportError(f'cannot load webserver implementation: {_IMPL_PATH}')
_IMPL = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _IMPL
_SPEC.loader.exec_module(_IMPL)

_PUBLIC_OVERRIDES = {
    'main', 'activate_openlitespeed', '_restore_service_state',
    '_service_enabled', '_service_enablement_state',
    '_replace_atomic', '_apply_expected_selinux_context',
    '_preparation_snapshots', '_restore_preparation',
}
for _name in dir(_IMPL):
    if _name not in _PUBLIC_OVERRIDES and _name not in globals():
        globals()[_name] = getattr(_IMPL, _name)


_ENABLED_STATES = {'enabled', 'enabled-runtime'}
_KNOWN_ENABLEMENT_STATES = {
    'enabled', 'enabled-runtime', 'disabled', 'static', 'indirect',
    'generated', 'transient', 'alias', 'linked', 'linked-runtime',
    'masked', 'masked-runtime', 'not-found',
}
_RESTORABLE_ENABLEMENT_STATES = {'enabled', 'enabled-runtime', 'disabled'}


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


def _replace_atomic(
    path: pathlib.Path, text: str, mode: int, uid: int, gid: int,
    xattrs: dict[str, bytes] | None,
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
        if xattrs is not None:
            _apply_xattrs(temporary, xattrs)
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


def _service_enablement_state(name: str) -> str:
    completed = run(['systemctl', 'is-enabled', name], check=False)
    state = _command_text(completed.stdout).lower()
    error = _command_text(completed.stderr)
    if (
        completed.returncode in {0, 1, 3, 4}
        and state in _KNOWN_ENABLEMENT_STATES
        and not error
    ):
        return state
    detail = error or state or str(completed.returncode)
    raise BuildError(f'could not determine enabled state for {name}: {detail}')


def _service_enabled(name: str) -> bool:
    return _service_enablement_state(name) in _ENABLED_STATES


def _restore_service_state(
    name: str, was_active: bool, enablement_state: str,
    *, was_runtime_masked: bool = False,
) -> list[str]:
    """Attempt every boot, runtime, verification, and mask restoration phase."""
    errors: list[str] = []

    def attempt(label: str, function, *args):
        try:
            return function(*args)
        except Exception as exc:
            errors.append(f'{label}: {exc}')
            return None

    if enablement_state == 'enabled':
        attempt(
            f'systemctl enable {name}',
            run, ['systemctl', 'enable', name],
        )
    elif enablement_state == 'enabled-runtime':
        attempt(
            f'systemctl disable {name}',
            run, ['systemctl', 'disable', name],
        )
        attempt(
            f'systemctl enable --runtime {name}',
            run, ['systemctl', 'enable', '--runtime', name],
        )
    elif enablement_state == 'disabled':
        attempt(
            f'systemctl disable {name}',
            run, ['systemctl', 'disable', name],
        )
    else:
        errors.append(
            f'unsupported original enablement state for {name}: '
            f'{enablement_state}'
        )

    activity_action = 'start' if was_active else 'stop'
    attempt(
        f'systemctl {activity_action} {name}',
        run, ['systemctl', activity_action, name],
    )

    observed_enablement = attempt(
        f'verify enablement state for {name}',
        _service_enablement_state, name,
    )
    if (
        observed_enablement is not None
        and observed_enablement != enablement_state
    ):
        errors.append(
            f'enablement state for {name} is {observed_enablement}; '
            f'expected {enablement_state}'
        )
    observed_activity = attempt(
        f'verify active state for {name}', _service_active, name
    )
    if observed_activity is not None and observed_activity != was_active:
        errors.append(
            f'active state for {name} is {observed_activity}; '
            f'expected {was_active}'
        )

    if was_runtime_masked:
        attempt(
            f'systemctl mask --runtime {name}',
            run, ['systemctl', 'mask', '--runtime', name],
        )
        observed_mask = attempt(
            f'verify runtime mask for {name}',
            _service_enablement_state, name,
        )
        if observed_mask is not None and observed_mask != 'masked-runtime':
            errors.append(
                f'enablement state for {name} is {observed_mask}; '
                'expected masked-runtime'
            )
    return errors


def activate_openlitespeed() -> None:
    name = 'lsws.service'
    initial_enablement = _service_enablement_state(name)
    if initial_enablement == 'masked':
        raise BuildError(
            'OpenLiteSpeed is persistently masked; unmask it explicitly before activation'
        )

    was_runtime_masked = initial_enablement == 'masked-runtime'
    was_active: bool | None = None
    previous_enablement: str | None = None
    try:
        if was_runtime_masked:
            run(['systemctl', 'unmask', '--runtime', name])
            previous_enablement = _service_enablement_state(name)
        else:
            previous_enablement = initial_enablement
        if previous_enablement not in _RESTORABLE_ENABLEMENT_STATES:
            raise BuildError(
                f'OpenLiteSpeed has unsupported enablement state: '
                f'{previous_enablement}'
            )
        was_active = _service_active(name)
        run(['systemctl', 'enable', '--now', name])
        if not _service_active(name):
            raise BuildError('OpenLiteSpeed did not become active')
        if _service_enablement_state(name) != 'enabled':
            raise BuildError('OpenLiteSpeed did not become persistently enabled')
    except Exception as original_error:
        rollback_errors: list[str] = []
        if was_active is not None and previous_enablement is not None:
            rollback_errors.extend(
                _restore_service_state(
                    name,
                    was_active,
                    previous_enablement,
                    was_runtime_masked=was_runtime_masked,
                )
            )
        elif was_runtime_masked:
            try:
                run(['systemctl', 'mask', '--runtime', name])
            except Exception as exc:
                rollback_errors.append(
                    f'systemctl mask --runtime {name}: {exc}'
                )
        if rollback_errors:
            raise BuildError(
                'OpenLiteSpeed activation failed and service rollback also failed: '
                + '; '.join(rollback_errors)
            ) from original_error
        raise


_IMPL._replace_atomic = _replace_atomic
_IMPL._apply_expected_selinux_context = _apply_expected_selinux_context
_IMPL._service_enablement_state = _service_enablement_state
_IMPL._service_enabled = _service_enabled
_IMPL._restore_service_state = _restore_service_state
_IMPL.activate_openlitespeed = activate_openlitespeed


def _trusted_directory(path: pathlib.Path) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise BuildError(f'cannot inspect OpenLiteSpeed directory: {path}') from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise BuildError(f'unsafe OpenLiteSpeed directory: {path}')
    return metadata


def _trusted_directory_chain(path: pathlib.Path) -> None:
    current = path
    while True:
        _trusted_directory(current)
        if current.parent == current:
            return
        current = current.parent


def _snapshot_directory(path: pathlib.Path) -> tuple[object, ...]:
    if not os.path.lexists(path):
        return ('missing',)
    metadata = _trusted_directory(path)
    return (
        'directory', stat.S_IMODE(metadata.st_mode),
        metadata.st_uid, metadata.st_gid, _capture_xattrs(path),
    )


def _fsync_directory(path: pathlib.Path) -> None:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, 'O_DIRECTORY'):
        flags |= os.O_DIRECTORY
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _restore_directory(
    path: pathlib.Path, snapshot: tuple[object, ...]
) -> None:
    kind = snapshot[0]
    if kind == 'missing':
        if not os.path.lexists(path):
            return
        metadata = path.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise BuildError(
                f'cannot remove non-directory created during preparation: {path}'
            )
        path.rmdir()
        _fsync_parent(path)
        return
    if kind != 'directory':
        raise BuildError(f'invalid directory snapshot for {path}')

    created = False
    if not os.path.lexists(path):
        path.mkdir(mode=int(snapshot[1]))
        created = True
    metadata = path.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise BuildError(f'cannot restore unsafe OpenLiteSpeed directory: {path}')
    os.chown(path, int(snapshot[2]), int(snapshot[3]))
    os.chmod(path, int(snapshot[1]))
    _apply_xattrs(path, dict(snapshot[4]))
    _fsync_directory(path)
    if created:
        _fsync_parent(path)


def _directory_snapshot_paths() -> list[pathlib.Path]:
    targets = (
        OLS_ROOT / 'fcgi-bin',
        OLS_HOSTPANEL,
        OLS_VHOSTS,
        OLS_STATE_ROOT,
        OLS_MARKERS,
        OLS_LOGS,
    )
    paths: set[pathlib.Path] = set()
    for target in targets:
        current = target
        paths.add(current)
        while not os.path.lexists(current):
            if current.parent == current:
                raise BuildError(
                    f'cannot resolve trusted parent for OpenLiteSpeed path: {target}'
                )
            current = current.parent
            if not os.path.lexists(current):
                paths.add(current)
        _trusted_directory_chain(current)
    return sorted(paths, key=lambda item: (len(item.parts), str(item)))


def _preparation_snapshots() -> list[tuple[str, pathlib.Path, tuple[object, ...]]]:
    snapshots: list[tuple[str, pathlib.Path, tuple[object, ...]]] = []
    for path in _directory_snapshot_paths():
        snapshots.append(('directory', path, _snapshot_directory(path)))

    lsphp_link = OLS_ROOT / 'fcgi-bin/lsphp'
    paths = (
        (OLS_MAIN, False),
        (OLS_ADMIN, False),
        (OLS_REGISTRY, False),
        (lsphp_link, True),
        (LSPHP_STATE, False),
    )
    for path, allow_symlink in paths:
        snapshots.append(
            ('path', path, _snapshot_path(path, allow_symlink=allow_symlink))
        )
    return snapshots


def _restore_preparation(
    snapshots: list[tuple[str, pathlib.Path, tuple[object, ...]]]
) -> list[str]:
    errors: list[str] = []
    for kind, path, snapshot in reversed(snapshots):
        try:
            if kind == 'path':
                _restore_path(path, snapshot)
            elif kind == 'directory':
                _restore_directory(path, snapshot)
            else:
                raise BuildError(f'invalid preparation snapshot kind: {kind}')
        except Exception as exc:
            errors.append(f'{path}: {exc}')
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=tuple(MODE_MAP))
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args(argv)
    options = read_config(DEFAULT_CONFIG)
    if options['webserver'] != args.mode:
        raise BuildError('build.conf webserver value changed during reconciliation')

    target = MODE_MAP[args.mode]
    domains = managed_domains()
    if args.check:
        if args.mode == 'openlitespeed':
            check_openlitespeed(options)
        mismatches = [name for name in domains if webserver.mode_of(name) != target]
        if mismatches:
            print('\n'.join(mismatches))
            return 10
        print(f'All {len(domains)} managed domains use {args.mode}.')
        return 0

    preparation: list[tuple[str, pathlib.Path, tuple[object, ...]]] | None = None
    if args.mode == 'openlitespeed':
        preparation = _preparation_snapshots()
        try:
            prepare_openlitespeed(options)
        except Exception as original_error:
            rollback_errors = _restore_preparation(preparation)
            if rollback_errors:
                raise BuildError(
                    f'OpenLiteSpeed preparation failed ({original_error}); '
                    'outer rollback also failed: ' + '; '.join(rollback_errors)
                ) from original_error
            raise BuildError(
                f'OpenLiteSpeed preparation failed and was rolled back: '
                f'{original_error}'
            ) from original_error

    admin: dict[str, object] = {'role': 'admin', 'user_id': 0, 'username': 'root'}
    previous = {domain: webserver.mode_of(domain) for domain in domains}
    changed: list[str] = []
    try:
        for domain in domains:
            result = webserver.set_mode(domain, target, admin)
            if result.get('changed'):
                changed.append(domain)
        mismatches = [name for name in domains if webserver.mode_of(name) != target]
        if mismatches:
            raise BuildError(
                'post-switch validation failed for: ' + ', '.join(mismatches[:10])
            )
        if args.mode == 'openlitespeed':
            activate_openlitespeed()
    except Exception as original_error:
        rollback_errors = rollback_domains(changed, previous, admin)
        if preparation is not None:
            rollback_errors.extend(_restore_preparation(preparation))
        if rollback_errors:
            raise BuildError(
                f'webserver switch failed ({original_error}); rollback also failed: '
                + '; '.join(rollback_errors)
            ) from original_error
        raise BuildError(
            f'webserver switch failed and was rolled back: {original_error}'
        ) from original_error

    print(f'Applied {args.mode} to {len(domains)} domains ({len(changed)} changed).')
    return 0


class _ForwardingModule(types.ModuleType):
    """Keep test/runtime monkeypatches visible inside the preserved implementation."""

    def __setattr__(self, name: str, value) -> None:
        super().__setattr__(name, value)
        if name in _PUBLIC_OVERRIDES:
            return
        implementation = super().__getattribute__('_IMPL')
        if hasattr(implementation, name):
            setattr(implementation, name, value)


_module = sys.modules.get(__name__)
if _module is not None:
    _module.__class__ = _ForwardingModule


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except BuildError as exc:
        print(f'hostpanel-build-web failed: {exc}', file=sys.stderr)
        raise SystemExit(1)
