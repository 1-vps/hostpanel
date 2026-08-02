#!/usr/bin/env python3
"""Fail-closed legal metadata validation for HostPanel release publication."""

from __future__ import annotations

import argparse
import pathlib
import re
import stat

MAX_TEXT_BYTES = 512 * 1024
PLACEHOLDER_RE = re.compile(r"\[([^\[\]\r\n]{2,160})\]")
REQUIRED_LICENSE_LINE = "**License:** Proprietary — see [LICENSE](LICENSE)."


class LegalValidationError(SystemExit):
    """Raised when repository legal metadata is unsafe for publication."""


def _read_regular_text(path: pathlib.Path) -> str:
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise LegalValidationError(f"required legal file is missing: {path.name}") from exc
    if not stat.S_ISREG(metadata.st_mode) or path.is_symlink() or metadata.st_nlink != 1:
        raise LegalValidationError(f"required legal file is not a single regular file: {path.name}")
    if metadata.st_size <= 0 or metadata.st_size > MAX_TEXT_BYTES:
        raise LegalValidationError(f"required legal file has an unsafe size: {path.name}")
    try:
        return path.read_text(encoding="utf-8", errors="strict")
    except UnicodeError as exc:
        raise LegalValidationError(f"required legal file is not strict UTF-8: {path.name}") from exc


def unresolved_placeholders(license_text: str) -> tuple[str, ...]:
    return tuple(sorted({match.group(1).strip() for match in PLACEHOLDER_RE.finditer(license_text)}))


def validate_repository(repository_root: pathlib.Path) -> None:
    root = repository_root.resolve(strict=True)
    readme = _read_regular_text(root / "README.md")
    license_text = _read_regular_text(root / "LICENSE")

    if REQUIRED_LICENSE_LINE not in readme:
        raise LegalValidationError(
            "README.md must identify HostPanel as proprietary software governed by LICENSE"
        )
    if "**License:** MIT" in readme:
        raise LegalValidationError("README.md still contains the obsolete MIT license claim")
    if not license_text.startswith("HostPanel End User License Agreement"):
        raise LegalValidationError("LICENSE is not the reviewed HostPanel EULA")

    placeholders = unresolved_placeholders(license_text)
    if placeholders:
        rendered = ", ".join(f"[{value}]" for value in placeholders)
        raise LegalValidationError(
            "LICENSE contains unresolved legal placeholders; authorized legal input is required: "
            + rendered
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", default=".")
    return parser.parse_args()


def main() -> int:
    arguments = parse_args()
    validate_repository(pathlib.Path(arguments.repository_root))
    print("HostPanel legal release metadata is complete and internally consistent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
