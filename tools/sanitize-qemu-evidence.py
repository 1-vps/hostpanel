#!/usr/bin/env python3
"""Fail-closed redaction for QEMU evidence immediately before artifact upload."""

from __future__ import annotations

import os
import pathlib
import re
import stat
import sys
import tempfile
from collections.abc import Iterator

MAX_FILE_BYTES = 128 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024
_SECRET_FIELD = rb"(?:access[_-]?token|token|password|passwd|secret|api[_-]?key)"
_SECRET_VALUE = rb"(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s\"']+)"
_ASSIGNMENT_VALUE = rb"(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s&#;]+)"
_JSON_VALUE = rb"(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^,\s}]+)"
_TOKEN_VALUE = rb"(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^@\s/\"']+)"

_RULES: tuple[tuple[re.Pattern[bytes], bytes], ...] = (
    (
        re.compile(
            rb"\b([a-z][a-z0-9+.-]*://[^/\s:@]+:)[^@\s/]+@",
            re.IGNORECASE,
        ),
        rb"\1[REDACTED]@",
    ),
    (
        re.compile(
            rb"(\bauthorization\s*[:=]\s*(?:basic|bearer)\s+)" + _SECRET_VALUE,
            re.IGNORECASE,
        ),
        rb"\1[REDACTED]",
    ),
    (
        re.compile(
            rb"((?:^|[?&;\s])" + _SECRET_FIELD + rb"\s*=\s*)" + _ASSIGNMENT_VALUE,
            re.IGNORECASE | re.MULTILINE,
        ),
        rb"\1[REDACTED]",
    ),
    (
        re.compile(
            rb"((?:\"" + _SECRET_FIELD + rb"\"|'" + _SECRET_FIELD + rb"')\s*:\s*)"
            + _JSON_VALUE,
            re.IGNORECASE,
        ),
        rb"\1[REDACTED]",
    ),
    (
        re.compile(
            rb"(x-access-token\s*:\s*)" + _TOKEN_VALUE,
            re.IGNORECASE,
        ),
        rb"\1[REDACTED]",
    ),
    (
        re.compile(
            rb"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b",
            re.IGNORECASE,
        ),
        b"[REDACTED_GITHUB_TOKEN]",
    ),
    (
        re.compile(
            rb"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
            re.IGNORECASE | re.DOTALL,
        ),
        b"[REDACTED_PRIVATE_KEY]",
    ),
)

_LEAK_PATTERNS: tuple[re.Pattern[bytes], ...] = (
    re.compile(
        rb"\b[a-z][a-z0-9+.-]*://[^/\s:@]+:(?!\[REDACTED\]@)[^@\s/]+@",
        re.IGNORECASE,
    ),
    re.compile(
        rb"\bauthorization\s*[:=]\s*(?:basic|bearer)\s+"
        rb"(?!\[REDACTED\])"
        + _SECRET_VALUE,
        re.IGNORECASE,
    ),
    re.compile(
        rb"(?:^|[?&;\s])"
        + _SECRET_FIELD
        + rb"\s*=\s*(?!\[REDACTED\])"
        + _ASSIGNMENT_VALUE,
        re.IGNORECASE | re.MULTILINE,
    ),
    re.compile(
        rb"(?:\"" + _SECRET_FIELD + rb"\"|'" + _SECRET_FIELD + rb"')\s*:\s*"
        rb"(?!\[REDACTED\])"
        + _JSON_VALUE,
        re.IGNORECASE,
    ),
    re.compile(
        rb"x-access-token\s*:\s*(?!\[REDACTED\])" + _TOKEN_VALUE,
        re.IGNORECASE,
    ),
    re.compile(
        rb"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b",
        re.IGNORECASE,
    ),
    re.compile(rb"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----", re.IGNORECASE),
)


def _sanitize(data: bytes) -> bytes:
    sanitized = data
    for pattern, replacement in _RULES:
        sanitized = pattern.sub(replacement, sanitized)
    for pattern in _LEAK_PATTERNS:
        if pattern.search(sanitized):
            raise RuntimeError("secret-shaped content remains after sanitization")
    return sanitized


def _require_safe_name(path: pathlib.Path) -> None:
    encoded_name = os.fsencode(path.name)
    try:
        sanitized_name = _sanitize(encoded_name)
    except RuntimeError as error:
        raise RuntimeError(
            "evidence entry name contains secret-shaped content"
        ) from error
    if sanitized_name != encoded_name:
        raise RuntimeError("evidence entry name contains secret-shaped content")


def _regular_files(root: pathlib.Path) -> Iterator[pathlib.Path]:
    for current, directory_names, file_names in os.walk(
        root,
        topdown=True,
        followlinks=False,
    ):
        directory = pathlib.Path(current)
        for name in directory_names:
            candidate = directory / name
            _require_safe_name(candidate)
            metadata = candidate.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise RuntimeError(f"unsafe evidence directory: {candidate}")
        for name in file_names:
            candidate = directory / name
            _require_safe_name(candidate)
            metadata = candidate.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                raise RuntimeError(f"unsafe evidence file: {candidate}")
            if metadata.st_nlink != 1:
                raise RuntimeError(f"evidence file has multiple links: {candidate}")
            yield candidate


def _read_stable(path: pathlib.Path) -> bytes:
    descriptor = os.open(
        path,
        os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise RuntimeError(f"evidence file changed before reading: {path}")
        if metadata.st_size > MAX_FILE_BYTES:
            raise RuntimeError(f"evidence file exceeds size limit: {path}")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            data = handle.read(metadata.st_size)
            if len(data) != metadata.st_size:
                raise RuntimeError(f"evidence file shrank while reading: {path}")
            if handle.read(1):
                raise RuntimeError(f"evidence file grew while reading: {path}")
        return data
    finally:
        os.close(descriptor)


def _replace_atomically(path: pathlib.Path, content: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.sanitize.",
        dir=path.parent,
    )
    temporary_path = pathlib.Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, path)
        metadata = path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise RuntimeError(f"sanitized evidence replacement is unsafe: {path}")
    finally:
        temporary_path.unlink(missing_ok=True)


def sanitize_tree(root: pathlib.Path) -> tuple[int, int]:
    metadata = root.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise RuntimeError(f"unsafe evidence root: {root}")

    file_count = 0
    changed_count = 0
    total_bytes = 0
    for path in _regular_files(root):
        original = _read_stable(path)
        total_bytes += len(original)
        if total_bytes > MAX_TOTAL_BYTES:
            raise RuntimeError("evidence tree exceeds total size limit")
        sanitized = _sanitize(original)
        if sanitized != original:
            _replace_atomically(path, sanitized)
            changed_count += 1
        file_count += 1
    return file_count, changed_count


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {argv[0]} EVIDENCE_DIRECTORY", file=sys.stderr)
        return 2
    root = pathlib.Path(argv[1])
    try:
        file_count, changed_count = sanitize_tree(root)
    except (OSError, RuntimeError) as error:
        print(f"QEMU evidence sanitization failed: {error}", file=sys.stderr)
        return 1
    print(
        f"QEMU evidence sanitization passed: {file_count} files, "
        f"{changed_count} rewritten."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
