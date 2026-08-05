#!/usr/bin/env python3
"""Fail closed when HostPanel release metadata and maintained docs diverge."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "RELEASE-MANIFEST.json"
VERSION_PATH = ROOT / "RELEASE_VERSION"
MAINTAINED_DOCS = (
    ROOT / "README.md",
    ROOT / "CONFIGURATION.md",
    ROOT / "PRODUCTION_READINESS.md",
)
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
SIGNED_BASE_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+-hardened-r[0-9]+$")
ALLOWED_CHANNELS = {"development", "candidate", "stable"}
ALLOWED_STATUSES = {
    "development",
    "deployable-not-publishable",
    "production-ready",
    "withdrawn",
}


class ValidationError(RuntimeError):
    """Raised for release metadata that must block publication."""


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


def validate_manifest(manifest: dict[str, Any]) -> tuple[str, str, str, bool]:
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
    publish_allowed = require_bool(release, "production_publish_allowed", "release")

    if not VERSION_RE.fullmatch(version):
        raise ValidationError("release.version must use MAJOR.MINOR.PATCH")
    if not SIGNED_BASE_RE.fullmatch(signed_base):
        raise ValidationError("release.signed_base must identify the hardened signed base")
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
            raise ValidationError(f"release.blockers[{index}] must be an object")
        blocker_id = require_text(blocker, "id", f"release.blockers[{index}]")
        require_text(blocker, "description", f"release.blockers[{index}]")
        if blocker_id in blocker_ids:
            raise ValidationError(f"duplicate release blocker id: {blocker_id}")
        blocker_ids.add(blocker_id)

    if publish_allowed and blockers:
        raise ValidationError("production publication cannot be allowed while blockers remain")
    if not publish_allowed and not blockers:
        raise ValidationError("blocked production publication must name at least one blocker")
    if status == "production-ready" and not publish_allowed:
        raise ValidationError("production-ready status requires production publication to be allowed")
    if status != "production-ready" and publish_allowed:
        raise ValidationError("production publication requires production-ready status")

    platform = manifest.get("platform")
    if not isinstance(platform, dict):
        raise ValidationError("platform must be an object")
    for key in ("architectures", "operating_systems"):
        values = platform.get(key)
        if not isinstance(values, list) or not values or not all(
            isinstance(item, str) and item.strip() for item in values
        ):
            raise ValidationError(f"platform.{key} must be a non-empty string array")
        if len(values) != len(set(values)):
            raise ValidationError(f"platform.{key} contains duplicates")
    for key in ("minimum_ram_mib", "minimum_root_free_mib"):
        value = platform.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValidationError(f"platform.{key} must be a positive integer")

    return version, signed_base, status, publish_allowed


def validate_version_file(version: str) -> None:
    try:
        version_file = VERSION_PATH.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise ValidationError(f"cannot read {VERSION_PATH.name}: {exc}") from exc
    if version_file != version:
        raise ValidationError(
            f"{VERSION_PATH.name} is {version_file!r}, expected {version!r}"
        )


def validate_docs(version: str, signed_base: str, status: str, publish_allowed: bool) -> None:
    required_tokens = {
        "{{HOSTPANEL_RELEASE_VERSION}}": version,
        "{{HOSTPANEL_SIGNED_BASE}}": signed_base,
        "{{HOSTPANEL_RELEASE_STATUS}}": status,
        "{{HOSTPANEL_PUBLICATION_ALLOWED}}": str(publish_allowed).lower(),
    }

    for path in MAINTAINED_DOCS:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise ValidationError(f"cannot read {path.name}: {exc}") from exc
        for marker, expected in required_tokens.items():
            declaration = f"{marker}={expected}"
            if declaration not in text:
                raise ValidationError(f"{path.name} is missing authoritative marker {declaration}")

        stale_installed = re.findall(
            r"(?:installed(?: application)? version|/opt/hostpanel/VERSION)[^\n`]*[`* ]+([0-9]+\.[0-9]+\.[0-9]+)",
            text,
            flags=re.IGNORECASE,
        )
        conflicting = sorted({found for found in stale_installed if found != version})
        if conflicting:
            raise ValidationError(
                f"{path.name} contains conflicting installed version(s): {', '.join(conflicting)}"
            )


def main() -> int:
    try:
        manifest = load_json(MANIFEST_PATH)
        version, signed_base, status, publish_allowed = validate_manifest(manifest)
        validate_version_file(version)
        validate_docs(version, signed_base, status, publish_allowed)
    except ValidationError as exc:
        print(f"release consistency: FAIL: {exc}", file=sys.stderr)
        return 1

    print(
        "release consistency: PASS "
        f"version={version} signed_base={signed_base} status={status} "
        f"production_publish_allowed={str(publish_allowed).lower()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
