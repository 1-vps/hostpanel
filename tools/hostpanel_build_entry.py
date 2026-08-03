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

_BASE_APPLY_BUILD = cli.apply_build


def config_path(argv: Sequence[str]) -> pathlib.Path:
    values = list(argv)
    for index, value in enumerate(values):
        if value == '--config' and index + 1 < len(values):
            return pathlib.Path(values[index + 1])
        if value.startswith('--config='):
            return pathlib.Path(value.split('=', 1)[1])
    return DEFAULT_CONFIG


def install_runtime_adapters(selected_config: pathlib.Path) -> None:
    powerdns_adapter.install()
    mongodb_adapter.install()
    cli.apply_mongodb = state.apply_mongodb
    cli.apply_varnish = state.apply_varnish

    def validate_mongodb(log_path: pathlib.Path) -> None:
        state.validate_mongodb(read_config(selected_config), log_path)

    cli.validate_mongodb = validate_mongodb

    def guarded_apply_build(
        component, options, platform, log_path, backup_dir,
        python_path, doctor_path, roles, web_helper, mode_file,
    ):
        state.ensure_safe_web_switch(component, options, mode_file)
        return powerdns_adapter.guarded_apply_build(
            _BASE_APPLY_BUILD,
            component, options, platform, log_path, backup_dir,
            python_path, doctor_path, roles, web_helper, mode_file,
        )

    cli.apply_build = guarded_apply_build


def main(argv: Sequence[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    install_runtime_adapters(config_path(values))
    return cli.main(values)
