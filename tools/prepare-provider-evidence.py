#!/usr/bin/env python3
"""Prepare a private provider-acceptance evidence directory fail-closed."""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from types import ModuleType


PREPARER_PATH = pathlib.Path(__file__).with_name("prepare-qemu-evidence.py")


def _load_preparer() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "provider_evidence_preparer_base",
        PREPARER_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load the reviewed evidence preparer")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PREPARER = _load_preparer()
PREPARER.ARTIFACTS_DIRECTORY = "provider-artifacts"
PREPARER.EVIDENCE_DIRECTORY = "evidence"
PREPARER.MARKER_NAME = "runner-evidence-state.txt"
PREPARER.MARKER_CONTENT = (
    b"No provider evidence was produced before the always-run sealing step.\n"
)


def prepare() -> None:
    PREPARER.prepare()


def main() -> int:
    try:
        prepare()
    except RuntimeError as error:
        print(f"Provider evidence preparation failed: {error}", file=sys.stderr)
        return 1
    except OSError as error:
        error_number = error.errno if error.errno is not None else "unknown"
        print(
            f"Provider evidence preparation failed: filesystem error ({error_number})",
            file=sys.stderr,
        )
        return 1
    print("Provider evidence preparation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
