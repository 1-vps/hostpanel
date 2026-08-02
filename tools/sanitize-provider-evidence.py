#!/usr/bin/env python3
"""Fail-closed redaction for provider-acceptance evidence."""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from types import ModuleType


SANITIZER_PATH = pathlib.Path(__file__).with_name("sanitize-qemu-evidence.py")


def _load_sanitizer() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "provider_evidence_sanitizer_base",
        SANITIZER_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load the reviewed evidence sanitizer")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SANITIZER = _load_sanitizer()
sanitize_tree = SANITIZER.sanitize_tree


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {argv[0]} EVIDENCE_DIRECTORY", file=sys.stderr)
        return 2
    try:
        file_count, changed_count = sanitize_tree(pathlib.Path(argv[1]))
    except OSError as error:
        error_number = error.errno if error.errno is not None else "unknown"
        print(
            f"Provider evidence sanitization failed: filesystem error ({error_number})",
            file=sys.stderr,
        )
        return 1
    except RuntimeError as error:
        print(f"Provider evidence sanitization failed: {error}", file=sys.stderr)
        return 1
    print(
        f"Provider evidence sanitization passed: {file_count} files, "
        f"{changed_count} rewritten."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
