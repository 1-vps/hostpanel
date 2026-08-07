#!/usr/bin/env python3
"""Run the HostPanel release builder with a fail-closed publication legal gate."""

from __future__ import annotations

import os
import pathlib
import runpy
import sys

from build_update_release_impl import deployable_release_version
from release_legal import validate_repository

PUBLISH_WORKFLOW_NAME = "Publish signed HostPanel release"


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
