#!/usr/bin/env python3
"""Verify downloaded HostPanel release metadata against an expected commit."""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import stat

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
MAX_JSON_BYTES = 1024 * 1024
MAX_ARCHIVE_BYTES = 100 * 1024 * 1024


def regular_file_size(path: pathlib.Path, label: str, maximum: int) -> int:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise SystemExit(f"could not inspect {label}: {exc}") from exc
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise SystemExit(f"unsafe {label}: expected one regular file link")
    if metadata.st_size < 1 or metadata.st_size > maximum:
        raise SystemExit(
            f"unsafe {label} size: {metadata.st_size} bytes; maximum is {maximum}"
        )
    return metadata.st_size


def load_object(path: pathlib.Path, label: str) -> dict[str, object]:
    regular_file_size(path, label, MAX_JSON_BYTES)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"invalid {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise SystemExit(f"invalid {label}: expected a JSON object")
    return value


def sha256_file(path: pathlib.Path) -> str:
    regular_file_size(path, "release archive", MAX_ARCHIVE_BYTES)
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise SystemExit(f"could not hash release archive: {exc}") from exc
    return digest.hexdigest()


def verify(
    manifest_path: pathlib.Path,
    archive_path: pathlib.Path,
    metadata_path: pathlib.Path,
    expected_commit: str,
    expected_version: str,
) -> None:
    if COMMIT_RE.fullmatch(expected_commit) is None:
        raise SystemExit("expected commit must be a lowercase full SHA-1")
    manifest = load_object(manifest_path, "release manifest")
    if set(manifest) != {
        "schema",
        "product",
        "channel",
        "version",
        "commit",
        "tag",
        "archive",
    }:
        raise SystemExit("existing release manifest has an unexpected shape")
    archive = manifest.get("archive")
    if not isinstance(archive, dict) or set(archive) != {"name", "sha256", "signature"}:
        raise SystemExit("existing release manifest archive entry is invalid")

    checks = {
        "schema": manifest.get("schema") == 1,
        "product": manifest.get("product") == "hostpanel",
        "channel": manifest.get("channel") == "stable",
        "version": manifest.get("version") == expected_version,
        "tag": manifest.get("tag") == f"v{expected_version}",
        "commit": manifest.get("commit") == expected_commit,
        "archive name": archive.get("name") == archive_path.name,
        "signature name": archive.get("signature") == f"{archive_path.name}.sig",
    }
    for label, valid in checks.items():
        if not valid:
            raise SystemExit(f"existing release manifest {label} does not match")

    expected_digest = archive.get("sha256")
    if not isinstance(expected_digest, str) or SHA256_RE.fullmatch(expected_digest) is None:
        raise SystemExit("existing release manifest archive digest is invalid")
    actual_digest = sha256_file(archive_path)
    if actual_digest != expected_digest:
        raise SystemExit("existing release archive digest does not match its signed manifest")

    metadata = load_object(metadata_path, "release build metadata")
    metadata_checks = {
        "version": metadata.get("version") == expected_version,
        "tag": metadata.get("tag") == f"v{expected_version}",
        "commit": metadata.get("commit") == expected_commit,
        "archive": metadata.get("archive") == archive_path.name,
        "archive_sha256": metadata.get("archive_sha256") == actual_digest,
    }
    for label, valid in metadata_checks.items():
        if not valid:
            raise SystemExit(f"existing release build metadata {label} does not match")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=pathlib.Path, required=True)
    parser.add_argument("--archive", type=pathlib.Path, required=True)
    parser.add_argument("--metadata", type=pathlib.Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--version", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    verify(
        args.manifest,
        args.archive,
        args.metadata,
        args.commit,
        args.version,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
