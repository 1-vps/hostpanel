#!/usr/bin/env python3
"""Run the deterministic installer hardening transform with marker compatibility."""
from __future__ import annotations

import importlib.util
import pathlib
import sys

MODULE_PATH = pathlib.Path(__file__).with_name("harden_install_runtime.py")
SPEC = importlib.util.spec_from_file_location("hostpanel_hardener", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise SystemExit("could not load the installer hardening implementation")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

_original_regex_once = MODULE.regex_once


def _regex_once(text: str, pattern: str, replacement: str, label: str) -> str:
    # The external-repository replacement intentionally renames the section
    # marker before the firewall helper is injected. Keep the later fail-closed
    # match aligned with the transformed marker.
    if label == "timed firewall rollback helper":
        pattern = pattern.replace(
            "# ---- Enterprise Linux repositories",
            "# ---- External repositories",
        )
        replacement = replacement.replace(
            "# ---- Enterprise Linux repositories",
            "# ---- External repositories",
        )
    return _original_regex_once(text, pattern, replacement, label)


MODULE.regex_once = _regex_once


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: harden_install.py SOURCE DESTINATION")
    source = pathlib.Path(sys.argv[1])
    destination = pathlib.Path(sys.argv[2])
    if not source.is_file() or source.is_symlink():
        raise SystemExit(f"unsafe installer base: {source}")
    transformed = MODULE.harden(source.read_text(encoding="utf-8"))
    destination.write_text(transformed, encoding="utf-8")
    destination.chmod(0o700)


if __name__ == "__main__":
    main()
