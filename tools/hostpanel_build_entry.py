"""Runtime adapter for stateful optional CustomBuild components."""
from __future__ import annotations

import contextlib
import pathlib
import sys
from typing import Sequence

import hostpanel_build_cli as cli
import hostpanel_build_extras_state as state
import hostpanel_build_operations_adapter as operations_adapter
import hostpanel_build_optional_postcheck_adapter as optional_postcheck_adapter
import hostpanel_build_powerdns_adapter as powerdns_adapter
import hostpanel_build_mongodb_adapter as mongodb_adapter
import hostpanel_build_web_transaction_adapter as web_transaction_adapter
from hostpanel_build_config import (
    BuildError, DEFAULT_CONFIG, read_config, read_roles,
)

_BASE_EXECUTE_BUILD = cli.execute_build
_BASE_PRINT_PLAN = cli.print_plan
_BASE_VALIDATE_COMPONENT = cli.validate_component
_BASE_STATE_ATTEMPT = state._attempt
_CANDIDATE_STOP_LABELS = {
    'candidate MongoDB stop',
    'candidate Varnish stop',
}
_PDNS_PATH_INSTALL_OLD = (
    '[Install]\n'
    'WantedBy=multi-user.target\n'
)
_PDNS_PATH_INSTALL_NEW = (
    '[Install]\n'
    'WantedBy=pdns.service\n'
)


def config_path(argv: Sequence[str]) -> pathlib.Path:
    values = list(argv)
    for index, value in enumerate(values):
        if value == '--config' and index + 1 < len(values):
            return pathlib.Path(values[index + 1])
        if value.startswith('--config='):
            return pathlib.Path(value.split('=', 1)[1])
    return DEFAULT_CONFIG


def install_optional_rollback_guard() -> None:
    original = state._attempt
    if getattr(original, '_hostpanel_candidate_stop_guard', False) is True:
        return

    def guarded_attempt(errors, label, function, *args, **kwargs):
        if any(
            error.startswith(tuple(item + ':' for item in _CANDIDATE_STOP_LABELS))
            for error in errors
        ):
            return False
        if label in _CANDIDATE_STOP_LABELS and function is state._disable_now:
            kwargs = dict(kwargs)
            kwargs['allow_absent'] = True
        return original(errors, label, function, *args, **kwargs)

    guarded_attempt._hostpanel_candidate_stop_guard = True  # type: ignore[attr-defined]
    state._attempt = guarded_attempt


def guarded_optional_apply(function):
    def apply(*args, **kwargs):
        previous = state._attempt
        install_optional_rollback_guard()
        try:
            return function(*args, **kwargs)
        finally:
            state._attempt = previous

    apply._hostpanel_optional_rollback_guard = True  # type: ignore[attr-defined]
    if getattr(function, '_hostpanel_transactional_post_apply', False):
        apply._hostpanel_transactional_post_apply = True  # type: ignore[attr-defined]
    return apply


def install_powerdns_watcher_lifecycle_guard() -> None:
    """Keep the path watcher coupled to every PowerDNS start/restart."""
    operations = powerdns_adapter.operations
    original = operations.install_powerdns_units
    if getattr(original, '_hostpanel_pdns_watcher_lifecycle_guard', False):
        return

    def install_units(managed_conf, zone_dir, dns_group):
        base_writer = operations.write_atomic_root
        path_unit_written = False

        def lifecycle_writer(path, text, mode=0o644):
            nonlocal path_unit_written
            if path == operations.PDNS_PATH_UNIT:
                if text.count(_PDNS_PATH_INSTALL_OLD) != 1:
                    raise BuildError(
                        'unexpected PowerDNS path-unit install dependency shape'
                    )
                text = text.replace(
                    _PDNS_PATH_INSTALL_OLD, _PDNS_PATH_INSTALL_NEW, 1
                )
                path_unit_written = True
            return base_writer(path, text, mode)

        operations.write_atomic_root = lifecycle_writer
        try:
            result = original(managed_conf, zone_dir, dns_group)
        finally:
            operations.write_atomic_root = base_writer
        if not path_unit_written:
            raise BuildError('PowerDNS path unit was not installed')
        return result

    install_units._hostpanel_pdns_watcher_lifecycle_guard = True  # type: ignore[attr-defined]
    operations.install_powerdns_units = install_units


def install_powerdns_watcher_enablement_guard() -> None:
    """Reset stale install symlinks before enabling the PowerDNS watcher."""
    original = powerdns_adapter._enable_and_start
    if getattr(original, '_hostpanel_pdns_watcher_reenable_guard', False):
        return
    path_service = powerdns_adapter.operations.PDNS_PATH_UNIT.name

    def enable_and_start(name: str, log_path: pathlib.Path) -> None:
        if name == path_service:
            powerdns_adapter._set_enabled(name, False, log_path)
        original(name, log_path)

    enable_and_start._hostpanel_pdns_watcher_reenable_guard = True  # type: ignore[attr-defined]
    powerdns_adapter._enable_and_start = enable_and_start


def _validate_dns_unit_state(
    unit: str, *, active: bool, enabled: bool
) -> None:
    observed_active = powerdns_adapter.service_active(unit)
    if observed_active != active:
        raise BuildError(
            f'{unit} active state is {observed_active}; expected {active}'
        )
    observed_enabled = powerdns_adapter.service_enabled(unit)
    if observed_enabled != enabled:
        raise BuildError(
            f'{unit} enabled state is {observed_enabled}; expected {enabled}'
        )


def validate_dns_runtime(options, platform) -> None:
    configured = options.get('dns', 'bind')
    if configured not in {'bind', 'powerdns'}:
        raise BuildError('dns must be bind or powerdns')
    applied = powerdns_adapter.applied_dns_mode()
    if applied != configured:
        raise BuildError('DNS runtime mode does not match build.conf')

    _, _, _, bind_service = powerdns_adapter.operations.dns_layout(platform)
    path_service = powerdns_adapter.operations.PDNS_PATH_UNIT.name
    powerdns_selected = configured == 'powerdns'
    _validate_dns_unit_state(
        'pdns.service', active=powerdns_selected, enabled=powerdns_selected
    )
    _validate_dns_unit_state(
        bind_service, active=not powerdns_selected, enabled=not powerdns_selected
    )
    _validate_dns_unit_state(
        path_service, active=powerdns_selected, enabled=powerdns_selected
    )


def _validate_optional_unit_state(
    unit: str, *, active: bool, enabled: bool
) -> None:
    observed_active = state._service_active(unit)
    if observed_active != active:
        raise BuildError(
            f'{unit} active state is {observed_active}; expected {active}'
        )
    observed_enabled = state._service_enabled(unit)
    if observed_enabled != enabled:
        raise BuildError(
            f'{unit} enabled state is {observed_enabled}; expected {enabled}'
        )


def validate_applied_runtime(parsed) -> None:
    roles = read_roles(pathlib.Path(parsed.roles_file))
    if 'dns' in roles:
        platform = cli.detect_platform(pathlib.Path(parsed.os_release))
        dns_mode = powerdns_adapter.applied_dns_mode()
        validate_dns_runtime({'dns': dns_mode}, platform)

    mongodb_mode = state._runtime_mode(
        state.MONGODB_MODE_FILE, 'off'
    )
    if mongodb_mode not in {'off', state.MONGODB_VERSION}:
        raise BuildError('invalid applied MongoDB mode')
    mongodb_enabled = mongodb_mode == state.MONGODB_VERSION
    _validate_optional_unit_state(
        'mongod.service',
        active=mongodb_enabled,
        enabled=mongodb_enabled,
    )

    varnish_mode = state._runtime_mode(state.VARNISH_MODE_FILE, 'off')
    if varnish_mode not in {'off', 'on'}:
        raise BuildError('invalid applied Varnish mode')
    varnish_enabled = varnish_mode == 'on'
    _validate_optional_unit_state(
        'varnish.service',
        active=varnish_enabled,
        enabled=varnish_enabled,
    )


def install_runtime_adapters(selected_config: pathlib.Path) -> None:
    operations_adapter.install()
    powerdns_adapter.install()
    install_powerdns_watcher_lifecycle_guard()
    install_powerdns_watcher_enablement_guard()
    mongodb_adapter.install()
    state.install()
    cli.apply_mongodb = guarded_optional_apply(state.apply_mongodb)
    cli.apply_varnish = guarded_optional_apply(state.apply_varnish)

    def validate_mongodb(log_path: pathlib.Path) -> None:
        state.validate_mongodb(read_config(selected_config), log_path)

    def checked_print_plan(
        component, options, platform, roles=None,
    ):
        mongodb_requested = options.get('mongodb') == '8.0' and (
            component == 'mongodb'
            or (component == 'all' and roles is not None and 'database' in roles)
        )
        if mongodb_requested:
            mongodb_adapter.mongodb_supported(platform)
        return _BASE_PRINT_PLAN(component, options, platform, roles)

    def validating_component(
        component, options, platform, log_path,
    ):
        result = _BASE_VALIDATE_COMPONENT(
            component, options, platform, log_path
        )
        if component == 'dns':
            validate_dns_runtime(options, platform)
        return result

    cli.validate_mongodb = validate_mongodb
    cli.validate_varnish = state.validate_varnish
    cli.validate_component = validating_component
    cli.print_plan = checked_print_plan

    def guarded_execute_build(
        component, options, platform, log_path, backup_dir,
        python_path, doctor_path, roles, web_helper, mode_file,
    ):
        state.ensure_safe_web_switch(component, options, mode_file)

        def optional_guarded_execute(
            inner_component, inner_options, inner_platform,
            inner_log_path, inner_backup_dir,
            inner_python_path, inner_doctor_path, inner_roles,
            inner_web_helper, inner_mode_file,
        ):
            return optional_postcheck_adapter.guarded_execute_build(
                _BASE_EXECUTE_BUILD,
                inner_component, inner_options, inner_platform,
                inner_log_path, inner_backup_dir,
                inner_python_path, inner_doctor_path, inner_roles,
                inner_web_helper, inner_mode_file,
            )

        def powerdns_guarded_execute(
            inner_component, inner_options, inner_platform,
            inner_log_path, inner_backup_dir,
            inner_python_path, inner_doctor_path, inner_roles,
            inner_web_helper, inner_mode_file,
        ):
            return powerdns_adapter.guarded_apply_build(
                optional_guarded_execute,
                inner_component, inner_options, inner_platform,
                inner_log_path, inner_backup_dir,
                inner_python_path, inner_doctor_path, inner_roles,
                inner_web_helper, inner_mode_file,
            )

        return web_transaction_adapter.guarded_execute_build(
            powerdns_guarded_execute,
            component, options, platform, log_path, backup_dir,
            python_path, doctor_path, roles, web_helper, mode_file,
        )

    cli.execute_build = guarded_execute_build


@contextlib.contextmanager
def _already_locked(_path: pathlib.Path):
    yield


def _requires_outer_lock(parsed) -> bool:
    command = parsed.command
    if command in {'set', 'validate', 'doctor'}:
        return True
    if command in {'build', 'update_versions', 'update_panel'}:
        return bool(getattr(parsed, 'apply', False))
    if command == 'ssl':
        return (
            parsed.ssl_command in {'issue', 'renew'}
            and bool(getattr(parsed, 'apply', False))
        )
    return False


@contextlib.contextmanager
def _command_runtime_guard(parsed):
    operations = operations_adapter.operations
    original_cli_doctor = cli.run_doctor
    original_operations_doctor = operations.run_doctor

    def checked_cli_doctor(log_path, python_path, doctor_path):
        result = original_cli_doctor(log_path, python_path, doctor_path)
        validate_applied_runtime(parsed)
        return result

    def checked_operations_doctor(log_path, python_path, doctor_path):
        result = original_operations_doctor(
            log_path, python_path, doctor_path
        )
        validate_applied_runtime(parsed)
        return result

    cli.run_doctor = checked_cli_doctor
    operations.run_doctor = checked_operations_doctor
    try:
        yield
    finally:
        cli.run_doctor = original_cli_doctor
        operations.run_doctor = original_operations_doctor


def _run_prelocked(values: list[str], lock_file: str) -> int:
    original_acquire_lock = cli.acquire_lock
    with original_acquire_lock(pathlib.Path(lock_file)):
        cli.acquire_lock = _already_locked
        try:
            return cli.main(values)
        finally:
            cli.acquire_lock = original_acquire_lock


def main(argv: Sequence[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    install_runtime_adapters(config_path(values))
    try:
        parsed = cli.parse_args(values)
        with _command_runtime_guard(parsed):
            if _requires_outer_lock(parsed):
                return _run_prelocked(values, parsed.lock_file)
            return cli.main(values)
    except (BuildError, OSError) as exc:
        print(f'hostpanel-build failed: {exc}', file=sys.stderr)
        return 1
