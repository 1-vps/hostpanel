#!/usr/bin/env python3
"""Run the source-release builder with phase-isolated overlay archives."""

from __future__ import annotations

import importlib.util
import os
import pathlib
import sys
from types import ModuleType
from typing import Sequence


BUILDER_PATH = pathlib.Path(__file__).with_name("build-source-release.py")


def load_builder() -> ModuleType:
    spec = importlib.util.spec_from_file_location("hostpanel_source_release_builder", BUILDER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load source release builder")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def overlay_archive_path(destination: pathlib.Path) -> pathlib.Path:
    if destination.name in ("", ".", ".."):
        raise ValueError("overlay destination must have a safe final component")
    return destination.parent / f"{destination.name}.tar"


def install_phase_isolation(builder: ModuleType) -> None:
    def export_overlay(
        root: pathlib.Path,
        commit: str,
        paths: Sequence[str],
        destination: pathlib.Path,
    ) -> None:
        for path in paths:
            builder.run_git(root, "cat-file", "-e", f"{commit}:{path}")
        archive_path = overlay_archive_path(destination)
        with archive_path.open("xb") as output:
            builder.run(
                [
                    "git",
                    "-C",
                    str(root),
                    "archive",
                    "--format=tar",
                    commit,
                    "--",
                    *paths,
                ],
                stdout=output,
            )
        builder.extract_tar_safely(
            archive_path,
            destination,
            require_single_root=False,
        )

    builder.export_overlay = export_overlay


def main(argv: Sequence[str] | None = None) -> int:
    builder = load_builder()
    install_phase_isolation(builder)
    return builder.main(argv)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        builder_error = getattr(sys.modules.get("hostpanel_source_release_builder"), "ReleaseBuildError", ())
        if builder_error and isinstance(error, builder_error):
            print(f"Source release build failed: {error}", file=os.sys.stderr)
            raise SystemExit(1)
        raise
