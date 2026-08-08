#!/usr/bin/env python3
"""Run the HostPanel release builder with a fail-closed publication legal gate."""

from __future__ import annotations

import importlib.util
import os
import pathlib
import runpy
import sys

PUBLISH_WORKFLOW_NAME = "Publish signed HostPanel release"


def _load_sibling(filename: str, module_name: str):
    """Load a sibling even when this wrapper itself is imported by file path."""
    path = pathlib.Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load release helper: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


validate_repository = _load_sibling(
    "release_legal.py", "hostpanel_release_legal"
).validate_repository
deployable_release_version = _load_sibling(
    "build_update_release_impl.py", "hostpanel_build_update_release_impl"
).deployable_release_version


def _repository_root(arguments: list[str]) -> pathlib.Path:
    for index, value in enumerate(arguments):
        if value == "--repository-root":
            if index + 1 >= len(arguments):
                raise SystemExit("--repository-root requires a value")
            return pathlib.Path(arguments[index + 1])
        if value.startswith("--repository-root="):
            return pathlib.Path(value.split("=", 1)[1])
    return pathlib.Path(".")


def _legal_gate_required() -> bool:
    return (
        os.environ.get("HP_REQUIRE_FINAL_LEGAL_TERMS") == "yes"
        or os.environ.get("GITHUB_WORKFLOW") == PUBLISH_WORKFLOW_NAME
    )


def main() -> None:
    if _legal_gate_required():
        validate_repository(_repository_root(sys.argv[1:]))
    implementation = pathlib.Path(__file__).with_name("build_update_release_impl.py")
    runpy.run_path(str(implementation), run_name="__main__")


if __name__ == "__main__":
    main()
