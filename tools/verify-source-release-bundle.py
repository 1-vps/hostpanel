#!/usr/bin/env python3
"""Verify a reproduced HostPanel source-release bundle fail closed."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import pathlib
import re
import stat
import sys
import tarfile
from types import ModuleType


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
SEMVER_RE = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
ATTESTATION_NAMES = (
    "sbom.cdx.json",
    "dependency-lock-attestation.json",
    "source-file-manifest.json",
)
PROVENANCE_NAME = "source-build.json"
CHECKSUM_NAME = "SOURCE-CANDIDATE-SHA256SUMS"
MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024
MAX_JSON_BYTES = 32 * 1024 * 1024
MAX_CHECKSUM_BYTES = 1024 * 1024
PROVENANCE_KEYS = {
    "schema",
    "product",
    "source_version",
    "release_id",
    "commit",
    "built_at",
    "baseline",
    "archive",
    "overlay_paths",
    "late_overlay_paths",
}


class BundleVerificationError(RuntimeError):
    """Raised when a source-release bundle is incomplete or inconsistent."""


def regular_file(path: pathlib.Path, label: str, maximum: int) -> int:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise BundleVerificationError(f"missing {label}: {path}") from exc
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise BundleVerificationError(f"unsafe {label}: expected one regular file link")
    if metadata.st_size <= 0 or metadata.st_size > maximum:
        raise BundleVerificationError(f"unsafe {label} size: {metadata.st_size}")
    return metadata.st_size


def sha256_file(path: pathlib.Path, *, maximum: int) -> str:
    expected_size = regular_file(path, path.name, maximum)
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            total += len(chunk)
            if total > maximum:
                raise BundleVerificationError(f"file exceeds size limit while hashing: {path.name}")
            digest.update(chunk)
    if total != expected_size:
        raise BundleVerificationError(f"file changed size while hashing: {path.name}")
    return digest.hexdigest()


def load_object(path: pathlib.Path, label: str) -> dict[str, object]:
    regular_file(path, label, MAX_JSON_BYTES)
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="strict"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BundleVerificationError(f"invalid {label}: {exc}") from exc
    if not isinstance(payload, dict):
        raise BundleVerificationError(f"invalid {label}: expected a JSON object")
    return payload


def require_semver(value: str, label: str) -> None:
    if SEMVER_RE.fullmatch(value) is None:
        raise BundleVerificationError(f"{label} is not a semantic version")


def require_utc_timestamp(value: object) -> None:
    if not isinstance(value, str):
        raise BundleVerificationError("source provenance built_at is not a string")
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError as exc:
        raise BundleVerificationError("source provenance built_at is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != dt.timedelta(0) or parsed.microsecond:
        raise BundleVerificationError("source provenance built_at is not canonical UTC")


def parse_checksums(path: pathlib.Path) -> dict[str, str]:
    regular_file(path, "source candidate checksums", MAX_CHECKSUM_BYTES)
    try:
        lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
    except (OSError, UnicodeError) as exc:
        raise BundleVerificationError("could not read source candidate checksums") from exc
    result: dict[str, str] = {}
    for line in lines:
        fields = line.split()
        if len(fields) != 2:
            raise BundleVerificationError("source candidate checksum line has an invalid shape")
        digest, name = fields
        if SHA256_RE.fullmatch(digest) is None:
            raise BundleVerificationError("source candidate checksum digest is invalid")
        pure = pathlib.PurePosixPath(name)
        if len(pure.parts) != 1 or pure.name != name or name in result:
            raise BundleVerificationError("source candidate checksum filename is unsafe or duplicated")
        result[name] = digest
    return result


def load_attestation_module(repository_root: pathlib.Path) -> ModuleType:
    module_path = repository_root / "tools" / "generate-source-attestations.py"
    regular_file(module_path, "source attestation verifier", MAX_JSON_BYTES)
    spec = importlib.util.spec_from_file_location(
        "hostpanel_source_bundle_attestations", module_path
    )
    if spec is None or spec.loader is None:
        raise BundleVerificationError("could not load source attestation verifier")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def verify_embedded_attestations(
    archive_path: pathlib.Path,
    directory: pathlib.Path,
    root_name: str,
) -> None:
    with tarfile.open(archive_path, "r:gz") as archive:
        members = {member.name.rstrip("/"): member for member in archive.getmembers()}
        for name in ATTESTATION_NAMES:
            member_name = f"{root_name}/{name}"
            member = members.get(member_name)
            if member is None or not member.isfile():
                raise BundleVerificationError(f"source archive is missing embedded {name}")
            handle = archive.extractfile(member)
            if handle is None:
                raise BundleVerificationError(f"could not read embedded {name}")
            with handle:
                embedded = handle.read(MAX_JSON_BYTES + 1)
            if len(embedded) != member.size or len(embedded) > MAX_JSON_BYTES:
                raise BundleVerificationError(f"embedded {name} has an unsafe size")
            standalone = (directory / name).read_bytes()
            if embedded != standalone:
                raise BundleVerificationError(
                    f"embedded and standalone {name} are not byte-identical"
                )


def verify(
    directory: pathlib.Path,
    repository_root: pathlib.Path,
    expected_commit: str,
    expected_source_version: str,
    expected_release_id: str,
) -> None:
    expected_commit = expected_commit.lower()
    if COMMIT_RE.fullmatch(expected_commit) is None:
        raise BundleVerificationError("expected commit must be a lowercase full SHA-1")
    require_semver(expected_source_version, "expected source version")
    require_semver(expected_release_id, "expected release identifier")
    source_core = ".".join(expected_source_version.split(".")[:3]).split("-", 1)[0]
    release_core = ".".join(expected_release_id.split(".")[:3]).split("-", 1)[0]
    if source_core != release_core:
        raise BundleVerificationError("expected source and release core versions differ")

    archive_name = f"hostpanel-v{expected_release_id}-source.tar.gz"
    expected_payloads = {
        archive_name,
        PROVENANCE_NAME,
        *ATTESTATION_NAMES,
    }
    checksum_path = directory / CHECKSUM_NAME
    checksums = parse_checksums(checksum_path)
    if set(checksums) != expected_payloads:
        raise BundleVerificationError(
            "source candidate checksums do not contain exactly the reviewed payload set"
        )

    maximums = {
        archive_name: MAX_ARCHIVE_BYTES,
        PROVENANCE_NAME: MAX_JSON_BYTES,
        **{name: MAX_JSON_BYTES for name in ATTESTATION_NAMES},
    }
    actual_digests: dict[str, str] = {}
    for name in sorted(expected_payloads):
        path = directory / name
        actual = sha256_file(path, maximum=maximums[name])
        if actual != checksums[name]:
            raise BundleVerificationError(f"source candidate checksum mismatch: {name}")
        actual_digests[name] = actual

    provenance = load_object(directory / PROVENANCE_NAME, "source provenance")
    if set(provenance) != PROVENANCE_KEYS:
        raise BundleVerificationError("source provenance has an unexpected shape")
    if provenance.get("schema") != 1 or provenance.get("product") != "hostpanel":
        raise BundleVerificationError("source provenance schema or product mismatch")
    identity_checks = {
        "commit": provenance.get("commit") == expected_commit,
        "source_version": provenance.get("source_version") == expected_source_version,
        "release_id": provenance.get("release_id") == expected_release_id,
    }
    for label, valid in identity_checks.items():
        if not valid:
            raise BundleVerificationError(f"source provenance {label} mismatch")
    require_utc_timestamp(provenance.get("built_at"))
    archive = provenance.get("archive")
    if not isinstance(archive, dict) or set(archive) != {"name", "sha256"}:
        raise BundleVerificationError("source provenance archive entry is invalid")
    if archive.get("name") != archive_name or archive.get("sha256") != actual_digests[archive_name]:
        raise BundleVerificationError("source provenance archive binding mismatch")
    baseline = provenance.get("baseline")
    if not isinstance(baseline, dict) or set(baseline) != {
        "archive",
        "release_id",
        "sha256",
    }:
        raise BundleVerificationError("source provenance baseline entry is invalid")
    if not isinstance(baseline.get("sha256"), str) or SHA256_RE.fullmatch(
        str(baseline.get("sha256"))
    ) is None:
        raise BundleVerificationError("source provenance baseline digest is invalid")
    for key in ("overlay_paths", "late_overlay_paths"):
        value = provenance.get(key)
        if not isinstance(value, list) or not value or not all(
            isinstance(item, str) and item for item in value
        ):
            raise BundleVerificationError(f"source provenance {key} is invalid")

    attestation_module = load_attestation_module(repository_root)
    try:
        attestation_module.verify_archive_attestations(
            directory / archive_name,
            root_name=f"hostpanel-v{expected_release_id}",
            source_version=expected_source_version,
            release_id=expected_release_id,
        )
    except Exception as exc:
        raise BundleVerificationError(f"source archive attestation verification failed: {exc}") from exc
    verify_embedded_attestations(
        directory / archive_name,
        directory,
        f"hostpanel-v{expected_release_id}",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=pathlib.Path, required=True)
    parser.add_argument("--repository-root", type=pathlib.Path, default=pathlib.Path("."))
    parser.add_argument("--commit", required=True)
    parser.add_argument("--source-version", required=True)
    parser.add_argument("--release-id", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        verify(
            args.directory.resolve(),
            args.repository_root.resolve(),
            args.commit,
            args.source_version,
            args.release_id,
        )
    except BundleVerificationError as exc:
        print(f"Source release bundle verification failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
