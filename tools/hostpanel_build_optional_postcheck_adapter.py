"""Run final optional-component doctor checks inside rollback boundaries."""
from __future__ import annotations

import pathlib

import hostpanel_build_cli as cli


def _postcheck_capable(function) -> bool:
    return bool(
        getattr(function, '_hostpanel_transactional_post_apply', False)
    )


def guarded_execute_build(
    function,
    component: str, options: dict[str, str], platform,
    log_path: pathlib.Path, backup_dir: pathlib.Path,
    python_path: pathlib.Path, doctor_path: pathlib.Path, roles: set[str],
    web_helper: pathlib.Path, mode_file: pathlib.Path,
):
    original_mongodb = cli.apply_mongodb
    original_varnish = cli.apply_varnish
    original_doctor = cli.run_doctor
    postchecks_completed = 0

    def wrap_optional(optional_function):
        capable = _postcheck_capable(optional_function)

        def apply(
            apply_options, apply_platform,
            apply_log_path, apply_backup_dir,
        ):
            nonlocal postchecks_completed
            if not capable:
                return optional_function(
                    apply_options, apply_platform,
                    apply_log_path, apply_backup_dir,
                )

            def post_apply() -> None:
                nonlocal postchecks_completed
                original_doctor(
                    apply_log_path, python_path, doctor_path
                )
                postchecks_completed += 1

            return optional_function(
                apply_options, apply_platform,
                apply_log_path, apply_backup_dir,
                post_apply=post_apply,
            )

        apply._hostpanel_optional_postcheck_wrapper = True  # type: ignore[attr-defined]
        return apply

    def final_doctor(
        final_log_path: pathlib.Path,
        final_python_path: pathlib.Path,
        final_doctor_path: pathlib.Path,
    ) -> None:
        if postchecks_completed:
            return
        original_doctor(
            final_log_path, final_python_path, final_doctor_path
        )

    cli.apply_mongodb = wrap_optional(original_mongodb)
    cli.apply_varnish = wrap_optional(original_varnish)
    cli.run_doctor = final_doctor
    try:
        return function(
            component, options, platform, log_path, backup_dir,
            python_path, doctor_path, roles, web_helper, mode_file,
        )
    finally:
        cli.apply_mongodb = original_mongodb
        cli.apply_varnish = original_varnish
        cli.run_doctor = original_doctor


__all__ = ['guarded_execute_build']
