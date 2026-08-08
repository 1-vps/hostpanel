#!/usr/bin/env python3
"""Fail closed when HostPanel release metadata, artifacts, and maintained docs diverge."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import sys
from pathlib import Path
from typing import Any

DEFAULT_ROOT = Path(__file__).resolve().parents[1]
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
SIGNED_BASE_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+-hardened-r[0-9]+$")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,200}$")
ALLOWED_CHANNELS = {"development", "candidate", "stable"}
ALLOWED_STATUSES = {
    "development",
    "deployable-not-publishable",
    "production-ready",
    "withdrawn",
}
MAINTAINED_DOC_NAMES = (
    "README.md",
    "CONFIGURATION.md",
    "PRODUCTION_READINESS.md",
)


class ValidationError(RuntimeError):
    """Raised for release state that must block publication."""


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValidationError(f"cannot read valid JSON from {path.name}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValidationError(f"{path.name} must contain one JSON object")
    return value


def require_text(mapping: dict[str, Any], key: str, context: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{context}.{key} must be a non-empty string")
    return value.strip()


def require_bool(mapping: dict[str, Any], key: str, context: str) -> bool:
    value = mapping.get(key)
    if not isinstance(value, bool):
        raise ValidationError(f"{context}.{key} must be a boolean")
    return value


def validate_manifest(
    manifest: dict[str, Any],
) -> tuple[str, str, str, bool, dict[str, Any]]:
    if manifest.get("schema_version") != 1:
        raise ValidationError("schema_version must be 1")
    if manifest.get("product") != "HostPanel":
        raise ValidationError("product must be HostPanel")

    release = manifest.get("release")
    if not isinstance(release, dict):
        raise ValidationError("release must be an object")

    version = require_text(release, "version", "release")
    signed_base = require_text(release, "signed_base", "release")
    channel = require_text(release, "channel", "release")
    status = require_text(release, "status", "release")
    publish_allowed = require_bool(
        release, "production_publish_allowed", "release"
    )

    if not VERSION_RE.fullmatch(version):
        raise ValidationError("release.version must use MAJOR.MINOR.PATCH")
    if not SIGNED_BASE_RE.fullmatch(signed_base):
        raise ValidationError(
            "release.signed_base must identify the hardened signed base"
        )
    if channel not in ALLOWED_CHANNELS:
        raise ValidationError(f"unsupported release.channel: {channel}")
    if status not in ALLOWED_STATUSES:
        raise ValidationError(f"unsupported release.status: {status}")

    blockers = release.get("blockers")
    if not isinstance(blockers, list):
        raise ValidationError("release.blockers must be an array")
    blocker_ids: set[str] = set()
    for index, blocker in enumerate(blockers):
        if not isinstance(blocker, dict):
            raise ValidationError(
                f"release.blockers[{index}] must be an object"
            )
        blocker_id = require_text(
            blocker, "id", f"release.blockers[{index}]"
        )
        require_text(
            blocker, "description", f"release.blockers[{index}]"
        )
        if blocker_id in blocker_ids:
            raise ValidationError(
                f"duplicate release blocker id: {blocker_id}"
            )
        blocker_ids.add(blocker_id)

    if publish_allowed and blockers:
        raise ValidationError(
            "production publication cannot be allowed while blockers remain"
        )
    if not publish_allowed and not blockers:
        raise ValidationError(
            "blocked production publication must name at least one blocker"
        )
    if status == "production-ready" and not publish_allowed:
        raise ValidationError(
            "production-ready status requires production publication to be allowed"
        )
    if status != "production-ready" and publish_allowed:
        raise ValidationError(
            "production publication requires production-ready status"
        )

    source_artifacts = manifest.get("source_artifacts")
    if not isinstance(source_artifacts, dict):
        raise ValidationError("source_artifacts must be an object")
    expected_artifact_keys = {
        "archive",
        "checksum_manifest",
        "signature",
    }
    if set(source_artifacts) != expected_artifact_keys:
        raise ValidationError(
            "source_artifacts must contain exactly archive, "
            "checksum_manifest, and signature"
        )
    for key in sorted(expected_artifact_keys):
        require_text(source_artifacts, key, "source_artifacts")

    platform = manifest.get("platform")
    if not isinstance(platform, dict):
        raise ValidationError("platform must be an object")
    for key in ("architectures", "operating_systems"):
        values = platform.get(key)
        if (
            not isinstance(values, list)
            or not values
            or not all(
                isinstance(item, str) and item.strip()
                for item in values
            )
        ):
            raise ValidationError(
                f"platform.{key} must be a non-empty string array"
            )
        if len(values) != len(set(values)):
            raise ValidationError(f"platform.{key} contains duplicates")
    for key in ("minimum_ram_mib", "minimum_root_free_mib"):
        value = platform.get(key)
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value <= 0
        ):
            raise ValidationError(
                f"platform.{key} must be a positive integer"
            )

    return version, signed_base, status, publish_allowed, source_artifacts


def safe_repository_file(root: Path, name: str, label: str) -> Path:
    if (
        not SAFE_NAME_RE.fullmatch(name)
        or Path(name).name != name
        or "/" in name
        or "\\" in name
    ):
        raise ValidationError(f"{label} must be a safe repository-root filename")
    path = root / name
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ValidationError(f"cannot inspect {label} {name}: {exc}") from exc
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise ValidationError(f"{label} is not a regular non-symlink file: {name}")
    return path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ValidationError(f"cannot hash {path.name}: {exc}") from exc
    return digest.hexdigest()


def validate_source_artifacts(
    root: Path,
    source_artifacts: dict[str, Any],
    signed_base: str,
) -> tuple[str, str]:
    archive_name = require_text(
        source_artifacts, "archive", "source_artifacts"
    )
    checksum_name = require_text(
        source_artifacts, "checksum_manifest", "source_artifacts"
    )
    signature_name = require_text(
        source_artifacts, "signature", "source_artifacts"
    )

    expected_archive = f"hostpanel-v{signed_base}-source.tar.gz"
    if archive_name != expected_archive:
        raise ValidationError(
            "source_artifacts.archive must match release.signed_base: "
            f"expected {expected_archive}, got {archive_name}"
        )

    archive = safe_repository_file(
        root, archive_name, "signed source archive"
    )
    checksum_manifest = safe_repository_file(
        root, checksum_name, "checksum manifest"
    )
    signature = safe_repository_file(
        root, signature_name, "checksum signature"
    )

    try:
        signature_size = signature.stat().st_size
    except OSError as exc:
        raise ValidationError(
            f"cannot inspect checksum signature: {exc}"
        ) from exc
    if signature_size != 64:
        raise ValidationError(
            "checksum signature must be the reviewed 64-byte raw signature"
        )

    try:
        lines = checksum_manifest.read_text(
            encoding="utf-8"
        ).splitlines()
    except (OSError, UnicodeError) as exc:
        raise ValidationError(
            f"cannot read checksum manifest: {exc}"
        ) from exc

    matches: list[str] = []
    for index, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            raise ValidationError(
                f"checksum manifest line {index} is malformed"
            )
        digest, filename = parts
        filename = filename.lstrip("*")
        if not DIGEST_RE.fullmatch(digest):
            raise ValidationError(
                f"checksum manifest line {index} has an invalid SHA-256"
            )
        if filename == archive_name:
            matches.append(digest)

    if len(matches) != 1:
        raise ValidationError(
            f"checksum manifest must contain exactly one entry for "
            f"{archive_name}; found {len(matches)}"
        )

    actual = sha256_file(archive)
    if actual != matches[0]:
        raise ValidationError(
            f"signed source archive checksum mismatch: "
            f"expected {matches[0]}, got {actual}"
        )
    return archive_name, actual


def validate_version_file(root: Path, version: str) -> None:
    path = root / "RELEASE_VERSION"
    try:
        version_file = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise ValidationError(f"cannot read {path.name}: {exc}") from exc
    if version_file != version:
        raise ValidationError(
            f"{path.name} is {version_file!r}, expected {version!r}"
        )


def validate_docs(
    root: Path,
    version: str,
    signed_base: str,
    status: str,
    publish_allowed: bool,
) -> None:
    required_tokens = {
        "{{HOSTPANEL_RELEASE_VERSION}}": version,
        "{{HOSTPANEL_SIGNED_BASE}}": signed_base,
        "{{HOSTPANEL_RELEASE_STATUS}}": status,
        "{{HOSTPANEL_PUBLICATION_ALLOWED}}": str(
            publish_allowed
        ).lower(),
    }

    for name in MAINTAINED_DOC_NAMES:
        path = root / name
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise ValidationError(
                f"cannot read {path.name}: {exc}"
            ) from exc
        for marker, expected in required_tokens.items():
            declaration = f"{marker}={expected}"
            if declaration not in text:
                raise ValidationError(
                    f"{path.name} is missing authoritative marker "
                    f"{declaration}"
                )

        stale_installed = re.findall(
            r"(?:installed(?: application)? version|"
            r"/opt/hostpanel/VERSION)[^\n`]*[`* ]+"
            r"([0-9]+\.[0-9]+\.[0-9]+)",
            text,
            flags=re.IGNORECASE,
        )
        conflicting = sorted(
            {found for found in stale_installed if found != version}
        )
        if conflicting:
            raise ValidationError(
                f"{path.name} contains conflicting installed "
                f"version(s): {', '.join(conflicting)}"
            )


def validate_repository(
    root: Path,
) -> tuple[str, str, str, bool, str, str]:
    root = root.resolve()
    manifest = load_json(root / "RELEASE-MANIFEST.json")
    (
        version,
        signed_base,
        status,
        publish_allowed,
        source_artifacts,
    ) = validate_manifest(manifest)
    archive_name, archive_digest = validate_source_artifacts(
        root, source_artifacts, signed_base
    )
    validate_version_file(root, version)
    validate_docs(
        root, version, signed_base, status, publish_allowed
    )
    return (
        version,
        signed_base,
        status,
        publish_allowed,
        archive_name,
        archive_digest,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_ROOT,
        help="repository root to validate",
    )
    args = parser.parse_args(argv)
    try:
        (
            version,
            signed_base,
            status,
            publish_allowed,
            archive_name,
            archive_digest,
        ) = validate_repository(args.root)
    except ValidationError as exc:
        print(f"release consistency: FAIL: {exc}", file=sys.stderr)
        return 1

    print(
        "release consistency: PASS "
        f"version={version} signed_base={signed_base} "
        f"status={status} "
        f"production_publish_allowed="
        f"{str(publish_allowed).lower()} "
        f"archive={archive_name} sha256={archive_digest}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
