#!/usr/bin/env python3
"""Fail-closed redaction for QEMU evidence immediately before artifact upload."""

from __future__ import annotations

import os
import pathlib
import re
import stat
import sys
import tempfile
import unicodedata
from collections.abc import Iterator
from typing import Any

MAX_FILE_BYTES = 128 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024
_SECRET_FIELD = rb"(?:access[_-]?token|token|password|passwd|secret|api[_-]?key)"
_DOUBLE_QUOTED = rb'"(?:\\.|[^"\\\r\n])*"'
_SINGLE_QUOTED = rb"'(?:\\.|[^'\\\r\n])*'"
_SECRET_VALUE = rb"(?:" + _DOUBLE_QUOTED + rb"|" + _SINGLE_QUOTED + rb"|[^\s\"']+)"
_ASSIGNMENT_VALUE = (
    rb"(?:" + _DOUBLE_QUOTED + rb"|" + _SINGLE_QUOTED + rb"|[^\s&#;]+)"
)
_JSON_VALUE = rb"(?:" + _DOUBLE_QUOTED + rb"|" + _SINGLE_QUOTED + rb"|[^,\s}]+)"
_TOKEN_VALUE = (
    rb"(?:" + _DOUBLE_QUOTED + rb"|" + _SINGLE_QUOTED + rb"|[^@\s/\"']+)"
)

_RULES: tuple[tuple[re.Pattern[bytes], bytes], ...] = (
    (
        re.compile(
            rb"\b([a-z][a-z0-9+.-]*://[^/\s:@]*:)[^@\s/]+@",
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
        rb'\1"[REDACTED]"',
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
        rb"\b[a-z][a-z0-9+.-]*://[^/\s:@]*:(?!\[REDACTED\]@)[^@\s/]+@",
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
        rb"(?!\"\[REDACTED\]\")"
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


def _safe_path(path: pathlib.Path) -> str:
    """Return a single-line ASCII representation safe for CI logs."""
    return ascii(str(path))


def _sanitize(data: bytes) -> bytes:
    sanitized = data
    for pattern, replacement in _RULES:
        sanitized = pattern.sub(replacement, sanitized)
    for pattern in _LEAK_PATTERNS:
        if pattern.search(sanitized):
            raise RuntimeError("secret-shaped content remains after sanitization")
    return sanitized


def _require_safe_name(path: pathlib.Path) -> None:
    if any(unicodedata.category(character) in {"Cc", "Cf"} for character in path.name):
        raise RuntimeError("evidence entry name contains control characters")

    encoded_name = os.fsencode(path.name)
    try:
        sanitized_name = _sanitize(encoded_name)
    except RuntimeError as error:
        raise RuntimeError(
            "evidence entry name contains secret-shaped content"
        ) from error
    if sanitized_name != encoded_name:
        raise RuntimeError("evidence entry name contains secret-shaped content")


def _walk_error(error: OSError) -> None:
    raise RuntimeError("could not traverse evidence directory") from error


def _regular_files(root: pathlib.Path) -> Iterator[pathlib.Path]:
    for current, directory_names, file_names in os.walk(
        root,
        topdown=True,
        followlinks=False,
        onerror=_walk_error,
    ):
        directory = pathlib.Path(current)
        for name in directory_names:
            candidate = directory / name
            _require_safe_name(candidate)
            metadata = candidate.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise RuntimeError(
                    f"unsafe evidence directory: {_safe_path(candidate)}"
                )
        for name in file_names:
            candidate = directory / name
            _require_safe_name(candidate)
            metadata = candidate.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                raise RuntimeError(f"unsafe evidence file: {_safe_path(candidate)}")
            if metadata.st_nlink != 1:
                raise RuntimeError(
                    f"evidence file has multiple links: {_safe_path(candidate)}"
                )
            yield candidate


def _metadata_signature(metadata: Any) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _read_pass(handle: Any, size: int, path: pathlib.Path) -> bytes:
    data = handle.read(size)
    if len(data) != size:
        raise RuntimeError(
            f"evidence file shrank while reading: {_safe_path(path)}"
        )
    if handle.read(1):
        raise RuntimeError(f"evidence file grew while reading: {_safe_path(path)}")
    return data


def _read_stable(path: pathlib.Path) -> bytes:
    descriptor = os.open(
        path,
        os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        initial = os.fstat(descriptor)
        if not stat.S_ISREG(initial.st_mode) or initial.st_nlink != 1:
            raise RuntimeError(
                f"evidence file changed before reading: {_safe_path(path)}"
            )
        if initial.st_size > MAX_FILE_BYTES:
            raise RuntimeError(
                f"evidence file exceeds size limit: {_safe_path(path)}"
            )
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            first_pass = _read_pass(handle, initial.st_size, path)
            handle.seek(0)
            second_pass = _read_pass(handle, initial.st_size, path)
        final = os.fstat(descriptor)
        if (
            first_pass != second_pass
            or _metadata_signature(initial) != _metadata_signature(final)
        ):
            raise RuntimeError(
                f"evidence file changed while reading: {_safe_path(path)}"
            )
        current_path = path.lstat()
        if _metadata_signature(initial) != _metadata_signature(current_path):
            raise RuntimeError(
                f"evidence file was replaced while reading: {_safe_path(path)}"
            )
        return first_pass
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
            raise RuntimeError(
                f"sanitized evidence replacement is unsafe: {_safe_path(path)}"
            )
    finally:
        temporary_path.unlink(missing_ok=True)


def sanitize_tree(root: pathlib.Path) -> tuple[int, int]:
    metadata = root.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise RuntimeError(f"unsafe evidence root: {_safe_path(root)}")

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
    except OSError as error:
        error_number = error.errno if error.errno is not None else "unknown"
        print(
            f"QEMU evidence sanitization failed: filesystem error ({error_number})",
            file=sys.stderr,
        )
        return 1
    except RuntimeError as error:
        print(f"QEMU evidence sanitization failed: {error}", file=sys.stderr)
        return 1
    print(
        f"QEMU evidence sanitization passed: {file_count} files, "
        f"{changed_count} rewritten."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
