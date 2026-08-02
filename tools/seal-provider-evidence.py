#!/usr/bin/env python3
"""Create an immutable sanitized provider-acceptance evidence snapshot."""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import tarfile
from types import ModuleType


SEALER_PATH = pathlib.Path(__file__).with_name("seal-qemu-evidence.py")
ARCHIVE_ROOT = pathlib.PurePosixPath("hostpanel-vps-acceptance")


def _load_sealer() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "provider_evidence_sealer_base",
        SEALER_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load the reviewed evidence sealer")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SEALER = _load_sealer()
SEALER.ARCHIVE_ROOT = ARCHIVE_ROOT
seal_tree = SEALER.seal_tree


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(
            f"usage: {argv[0]} EVIDENCE_DIRECTORY OUTPUT_ARCHIVE",
            file=sys.stderr,
        )
        return 2
    try:
        file_count, total_bytes = seal_tree(
            pathlib.Path(argv[1]),
            pathlib.Path(argv[2]),
        )
    except OSError as error:
        error_number = error.errno if error.errno is not None else "unknown"
        print(
            f"Provider evidence sealing failed: filesystem error ({error_number})",
            file=sys.stderr,
        )
        return 1
    except (RuntimeError, tarfile.TarError) as error:
        print(f"Provider evidence sealing failed: {error}", file=sys.stderr)
        return 1
    print(
        f"Provider evidence sealing passed: {file_count} files, "
        f"{total_bytes} sanitized bytes."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
