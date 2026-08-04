"""Outer transaction guard for complete HostPanel web builds."""
from __future__ import annotations

from dataclasses import dataclass
import os
import pathlib
import secrets
import stat

import hostpanel_build_operations as operations
from hostpanel_build_config import BuildError, Platform

_ENABLED_STATES = {'enabled', 'enabled-runtime'}
_NON_ENABLED_STATES = {
    'disabled', 'static', 'indirect', 'generated', 'transient',
    'alias', 'linked', 'linked-runtime', 'masked', 'masked-runtime',
    'not-found',
}
_RESTORABLE_STATES = {'enabled', 'enabled-runtime', 'disabled', 'not-found'}


@dataclass(frozen=True)
class ServiceState:
    active: bool
    enablement: str


def _command_text(value: object) -> str:
    if value is None:
        return ''
    if isinstance(value, bytes):
        return value.decode('utf-8', errors='replace').strip()
    return str(value).strip()


def _unit_absent(detail: str) -> bool:
    lowered = detail.lower()
    return any(
        marker in lowered
        for marker in (
            'does not exist', 'not found', 'not loaded',
            'no such file', 'could not be found',
        )
    )


def _service_active(name: str) -> bool:
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


def _service_enablement(name: str) -> str:
    completed = operations.run_command(
        ['systemctl', 'is-enabled', name], check=False, capture=True
    )
    state = _command_text(getattr(completed, 'stdout', '')).lower()
    error = _command_text(getattr(completed, 'stderr', ''))
    if completed.returncode == 0 and state in _ENABLED_STATES and not error:
        return state
    if (
        completed.returncode in {0, 1, 3, 4}
        and state in _NON_ENABLED_STATES
        and not error
    ):
        return state
    detail = error or state or str(completed.returncode)
    raise BuildError(f'could not determine enabled state for {name}: {detail}')


def _capture_service(name: str) -> ServiceState:
    before = _service_enablement(name)
    if before not in _RESTORABLE_STATES:
        raise BuildError(
            f'{name} has unsupported pre-build enablement state: {before}'
        )
    active = _service_active(name)
    after = _service_enablement(name)
    if before != after:
        raise BuildError(
            f'{name} enablement changed while its pre-build state was captured'
        )
    if before == 'not-found' and active:
        raise BuildError(f'{name} is active but its unit is not found')
    return ServiceState(active=active, enablement=before)


def _run_systemctl(
    arguments: list[str], log_path: pathlib.Path, *, allow_absent: bool = False
) -> None:
    completed = operations.run_command(
        arguments, check=False, capture=True, log_path=log_path
    )
    if completed.returncode == 0:
        return
    detail = (
        _command_text(getattr(completed, 'stderr', ''))
        or _command_text(getattr(completed, 'stdout', ''))
        or str(completed.returncode)
    )
    if allow_absent and _unit_absent(detail):
        return
    raise BuildError(f"{' '.join(arguments)} failed: {detail}")


def _restore_service(
    name: str, state: ServiceState, log_path: pathlib.Path
) -> list[str]:
    errors: list[str] = []

    def attempt(label: str, function, *args, **kwargs):
        try:
            return function(*args, **kwargs)
        except Exception as exc:
            errors.append(f'{label}: {exc}')
            return None

    allow_absent = state.enablement == 'not-found'
    if state.enablement == 'enabled':
        attempt(
            f'{name} enablement', _run_systemctl,
            ['systemctl', 'enable', name], log_path,
        )
    elif state.enablement == 'enabled-runtime':
        attempt(
            f'{name} persistent disable', _run_systemctl,
            ['systemctl', 'disable', name], log_path,
        )
        attempt(
            f'{name} runtime enable', _run_systemctl,
            ['systemctl', 'enable', '--runtime', name], log_path,
        )
    else:
        attempt(
            f'{name} disable', _run_systemctl,
            ['systemctl', 'disable', name], log_path,
            allow_absent=allow_absent,
        )

    action = 'start' if state.active else 'stop'
    attempt(
        f'{name} {action}', _run_systemctl,
        ['systemctl', action, name], log_path,
        allow_absent=allow_absent,
    )

    observed_enablement = attempt(
        f'{name} enablement verification', _service_enablement, name
    )
    accepted_enablement = (
        {'not-found', 'disabled'}
        if state.enablement == 'not-found'
        else {state.enablement}
    )
    if (
        observed_enablement is not None
        and observed_enablement not in accepted_enablement
    ):
        errors.append(
            f'{name} enablement is {observed_enablement}; '
            f'expected {state.enablement}'
        )
    observed_active = attempt(
        f'{name} activity verification', _service_active, name
    )
    if observed_active is not None and observed_active != state.active:
        errors.append(
            f'{name} active state is {observed_active}; '
            f'expected {state.active}'
        )
    return errors


def _trusted_directory(path: pathlib.Path, *, private: bool) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise BuildError(f'cannot inspect web transaction directory: {path}') from exc
    disallowed = 0o077 if private else 0o022
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid not in {0, os.geteuid()}
        or stat.S_IMODE(metadata.st_mode) & disallowed
    ):
        raise BuildError(f'unsafe web transaction directory: {path}')


def _trusted_directory_chain(path: pathlib.Path) -> None:
    current = path
    while True:
        _trusted_directory(current, private=False)
        if current.parent == current:
            return
        current = current.parent


def _fsync_parent(path: pathlib.Path) -> None:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, 'O_DIRECTORY'):
        flags |= os.O_DIRECTORY
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path.parent, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _transaction_state_path(backup_dir: pathlib.Path) -> pathlib.Path:
    _trusted_directory(backup_dir, private=True)
    transaction_dir = backup_dir / 'web-transactions'
    if not os.path.lexists(transaction_dir):
        transaction_dir.mkdir(mode=0o700)
        os.chown(transaction_dir, os.geteuid(), os.getegid())
        os.chmod(transaction_dir, 0o700)
        _fsync_parent(transaction_dir)
    _trusted_directory(transaction_dir, private=True)
    for _ in range(32):
        path = transaction_dir / f'web-{secrets.token_hex(16)}.json'
        if not os.path.lexists(path):
            return path
    raise BuildError('could not allocate a web transaction state path')


def _trusted_tool(path: pathlib.Path, *, executable: bool) -> None:
    _trusted_directory_chain(path.parent)
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise BuildError(f'cannot inspect web transaction tool: {path}') from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid not in {0, os.geteuid()}
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or (executable and not os.access(path, os.X_OK))
    ):
        raise BuildError(f'unsafe web transaction tool: {path}')


def _trusted_executable(path: pathlib.Path) -> pathlib.Path:
    _trusted_directory_chain(path.parent)
    try:
        link_metadata = path.lstat()
    except OSError as exc:
        raise BuildError(f'cannot inspect web transaction executable: {path}') from exc
    if stat.S_ISLNK(link_metadata.st_mode):
        if link_metadata.st_uid not in {0, os.geteuid()}:
            raise BuildError(f'unsafe web transaction executable symlink: {path}')
        try:
            resolved = path.resolve(strict=True)
        except OSError as exc:
            raise BuildError(
                f'cannot resolve web transaction executable: {path}'
            ) from exc
        _trusted_directory_chain(resolved.parent)
        metadata = resolved.lstat()
    else:
        resolved = path
        metadata = link_metadata
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid not in {0, os.geteuid()}
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or not os.access(resolved, os.X_OK)
    ):
        raise BuildError(f'unsafe web transaction executable: {path}')
    return resolved


def _state_helper(web_helper: pathlib.Path) -> pathlib.Path:
    helper = web_helper.with_name('hostpanel_build_web_state.py')
    _trusted_tool(helper, executable=False)
    return helper


def _discard_state(path: pathlib.Path) -> None:
    if not os.path.lexists(path):
        return
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise BuildError(f'unsafe web transaction state cleanup target: {path}')
    path.unlink()
    _fsync_parent(path)


def _helper_command(
    python_path: pathlib.Path,
    state_helper: pathlib.Path,
    action: str,
    state_path: pathlib.Path,
    log_path: pathlib.Path,
    *, mode_file: pathlib.Path | None = None,
) -> None:
    command = [str(python_path), str(state_helper), action, str(state_path)]
    if mode_file is not None:
        command.extend(['--mode-file', str(mode_file)])
    operations.run_command(command, log_path=log_path)


def _restore_helper_state(
    python_path: pathlib.Path,
    state_helper: pathlib.Path,
    state_path: pathlib.Path,
    log_path: pathlib.Path,
) -> list[str]:
    if not os.path.lexists(state_path):
        return []
    try:
        _helper_command(
            python_path, state_helper, '--restore', state_path, log_path
        )
        return []
    except Exception as exc:
        return [f'web runtime state: {exc}']


def guarded_execute_build(
    function,
    component: str, options: dict[str, str], platform: Platform,
    log_path: pathlib.Path, backup_dir: pathlib.Path,
    python_path: pathlib.Path, doctor_path: pathlib.Path, roles: set[str],
    web_helper: pathlib.Path, mode_file: pathlib.Path,
):
    web_requested = component == 'web' or (
        component == 'all' and 'web' in roles
    )
    if not web_requested:
        return function(
            component, options, platform, log_path, backup_dir,
            python_path, doctor_path, roles, web_helper, mode_file,
        )

    _trusted_executable(python_path)
    _trusted_tool(web_helper, executable=False)
    state_helper = _state_helper(web_helper)
    state_path = _transaction_state_path(backup_dir)
    apache = 'apache2.service' if platform.family == 'debian' else 'httpd.service'
    units = ('nginx.service', apache, 'lsws.service')
    service_states = {
        unit: _capture_service(unit)
        for unit in units
    }

    base_reconcile = operations.reconcile_webserver
    reconcile_called = False
    state_captured = False

    def transactional_reconcile(
        reconcile_options: dict[str, str], helper: pathlib.Path,
        reconcile_python: pathlib.Path, reconcile_mode_file: pathlib.Path,
        reconcile_log: pathlib.Path,
    ) -> None:
        nonlocal reconcile_called
        if reconcile_called:
            raise BuildError('webserver reconciliation ran more than once')
        if not state_captured:
            raise BuildError('web transaction state was not captured before reconciliation')
        if (
            helper != web_helper
            or reconcile_python != python_path
            or reconcile_mode_file != mode_file
        ):
            raise BuildError('webserver reconciliation inputs changed during transaction')
        _helper_command(
            python_path, state_helper, '--verify', state_path, reconcile_log
        )
        base_reconcile(
            reconcile_options, helper, reconcile_python,
            reconcile_mode_file, reconcile_log,
        )
        reconcile_called = True

    operations.reconcile_webserver = transactional_reconcile
    try:
        try:
            _helper_command(
                python_path, state_helper, '--capture', state_path,
                log_path, mode_file=mode_file,
            )
            state_captured = True
            result = function(
                component, options, platform, log_path, backup_dir,
                python_path, doctor_path, roles, web_helper, mode_file,
            )
            if not reconcile_called:
                raise BuildError('web build completed without webserver reconciliation')
        except Exception as original_error:
            rollback_errors = _restore_helper_state(
                python_path, state_helper, state_path, log_path
            )
            for unit in reversed(units):
                rollback_errors.extend(
                    _restore_service(unit, service_states[unit], log_path)
                )
            if not rollback_errors:
                try:
                    _discard_state(state_path)
                except Exception as exc:
                    rollback_errors.append(f'transaction state cleanup: {exc}')
            if rollback_errors:
                retained = (
                    f'; recovery state retained at {state_path}'
                    if os.path.lexists(state_path)
                    else ''
                )
                raise BuildError(
                    f'web build failed ({original_error}); outer rollback also failed: '
                    + '; '.join(rollback_errors) + retained
                ) from original_error
            raise
        try:
            _discard_state(state_path)
        except Exception as exc:
            raise BuildError(
                'web build completed, but transaction state cleanup failed: '
                f'{exc}; remove {state_path} after inspection'
            ) from exc
        return result
    finally:
        operations.reconcile_webserver = base_reconcile


__all__ = [
    'ServiceState', 'guarded_execute_build',
]
