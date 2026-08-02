#!/usr/bin/env python3
"""Fail-closed legal metadata validation for HostPanel release publication."""

from __future__ import annotations

import argparse
import os
import pathlib
import re
import stat

MAX_TEXT_BYTES = 512 * 1024
PLACEHOLDER_RE = re.compile(r"\[([^\[\]\r\n]{2,160})\]")
REQUIRED_LICENSE_LINE = "**License:** Proprietary — see [LICENSE](LICENSE)."


class LegalValidationError(SystemExit):
    """Raised when repository legal metadata is unsafe for publication."""


def _same_file(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        left.st_dev,
        left.st_ino,
        left.st_mode,
        left.st_uid,
        left.st_gid,
        left.st_nlink,
        left.st_size,
        left.st_mtime_ns,
    ) == (
        right.st_dev,
        right.st_ino,
        right.st_mode,
        right.st_uid,
        right.st_gid,
        right.st_nlink,
        right.st_size,
        right.st_mtime_ns,
    )


def _read_regular_text(path: pathlib.Path) -> str:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise LegalValidationError(f"required legal file cannot be opened safely: {path.name}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise LegalValidationError(
                f"required legal file is not a single regular file: {path.name}"
            )
        if before.st_size <= 0 or before.st_size > MAX_TEXT_BYTES:
            raise LegalValidationError(f"required legal file has an unsafe size: {path.name}")
        chunks: list[bytes] = []
        remaining = MAX_TEXT_BYTES + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        after = os.fstat(descriptor)
        if len(data) > MAX_TEXT_BYTES or len(data) != before.st_size:
            raise LegalValidationError(f"required legal file changed size while read: {path.name}")
        if not _same_file(before, after):
            raise LegalValidationError(f"required legal file changed while read: {path.name}")
    finally:
        os.close(descriptor)
    try:
        return data.decode("utf-8", errors="strict")
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
