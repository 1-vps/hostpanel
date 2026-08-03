"""Runtime adapter for stateful optional CustomBuild components."""
from __future__ import annotations

import pathlib
import sys
from typing import Sequence

import hostpanel_build_cli as cli
import hostpanel_build_extras_state as state
from hostpanel_build_config import DEFAULT_CONFIG, read_config


def config_path(argv: Sequence[str]) -> pathlib.Path:
    values = list(argv)
    for index, value in enumerate(values):
        if value == '--config' and index + 1 < len(values):
            return pathlib.Path(values[index + 1])
        if value.startswith('--config='):
            return pathlib.Path(value.split('=', 1)[1])
    return DEFAULT_CONFIG


def main(argv: Sequence[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    selected_config = config_path(values)

    cli.apply_mongodb = state.apply_mongodb
    cli.apply_varnish = state.apply_varnish

    def validate_mongodb(log_path: pathlib.Path) -> None:
        state.validate_mongodb(read_config(selected_config), log_path)

    cli.validate_mongodb = validate_mongodb

    original_apply_build = cli.apply_build

    def guarded_apply_build(
        component, options, platform, log_path, backup_dir,
        python_path, doctor_path, roles, web_helper, mode_file,
    ):
        state.ensure_safe_web_switch(component, options, mode_file)
        return original_apply_build(
            component, options, platform, log_path, backup_dir,
            python_path, doctor_path, roles, web_helper, mode_file,
        )

    cli.apply_build = guarded_apply_build
    return cli.main(values)
