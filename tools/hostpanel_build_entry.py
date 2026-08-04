"""Runtime adapter for stateful optional CustomBuild components."""
from __future__ import annotations

import pathlib
import sys
from typing import Sequence

import hostpanel_build_cli as cli
import hostpanel_build_extras_state as state
import hostpanel_build_powerdns_adapter as powerdns_adapter
import hostpanel_build_mongodb_adapter as mongodb_adapter
from hostpanel_build_config import DEFAULT_CONFIG, read_config

_BASE_EXECUTE_BUILD = cli.execute_build
_BASE_PRINT_PLAN = cli.print_plan
_BASE_STATE_ATTEMPT = state._attempt
_CANDIDATE_STOP_LABELS = {
    'candidate MongoDB stop',
    'candidate Varnish stop',
}


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
    return apply


def install_runtime_adapters(selected_config: pathlib.Path) -> None:
    powerdns_adapter.install()
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

    cli.validate_mongodb = validate_mongodb
    cli.validate_varnish = state.validate_varnish
    cli.print_plan = checked_print_plan

    def guarded_execute_build(
        component, options, platform, log_path, backup_dir,
        python_path, doctor_path, roles, web_helper, mode_file,
    ):
        state.ensure_safe_web_switch(component, options, mode_file)
        return powerdns_adapter.guarded_apply_build(
            _BASE_EXECUTE_BUILD,
            component, options, platform, log_path, backup_dir,
            python_path, doctor_path, roles, web_helper, mode_file,
        )

    cli.execute_build = guarded_execute_build


def main(argv: Sequence[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    install_runtime_adapters(config_path(values))
    try:
        return cli.main(values)
    except OSError as exc:
        print(f'hostpanel-build failed: {exc}', file=sys.stderr)
        return 1
