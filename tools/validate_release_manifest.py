#!/usr/bin/env python3
"""Fail closed when HostPanel release metadata, artifacts, and maintained docs diverge."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

DEFAULT_ROOT = Path(__file__).resolve().parents[1]
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
SIGNED_BASE_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+-hardened-r[0-9]+$")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,200}$")
SOURCE_TARBALL_RE = re.compile(
    r"^hostpanel-v[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z][0-9A-Za-z.-]*)?-source\.tar\.gz$"
)
ALLOWED_CHANNELS = {"development", "candidate", "stable"}
ALLOWED_STATUSES = {
    "development",
    "deployable-not-publishable",
    "production-ready",
    "withdrawn",
}
EXPECTED_AUTHORITATIVE_DOCUMENTS = {
    "README.md",
    "CONFIGURATION.md",
    "PRODUCTION_READINESS.md",
    "RELEASE_VERSION",
}
MAX_SMALL_FILE_BYTES = 8 * 1024 * 1024
MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024
TRUSTED_RELEASE_PUBLIC_KEY = b"""-----BEGIN PUBLIC KEY-----\nMCowBQYDK2VwAyEAJonL5vK2NRcFkXvKZUs64ISOs+FfhwL8gQVmFO4C0qk=\n-----END PUBLIC KEY-----\n"""


class ValidationError(RuntimeError):
    """Raised for release state that must block publication."""


def validate_safe_name(name: str, label: str) -> None:
    if (
        not SAFE_NAME_RE.fullmatch(name)
        or Path(name).name != name
        or "/" in name
        or "\\" in name
    ):
        raise ValidationError(f"{label} must be a safe repository-root filename")


def open_regular_fd(root: Path, name: str, label: str) -> tuple[int, os.stat_result]:
    validate_safe_name(name, label)
    path = root / name
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise ValidationError(f"cannot safely open {label} {name}: {exc}") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValidationError(f"{label} is not a single regular file: {name}")
        return fd, info
    except Exception:
        os.close(fd)
        raise


def read_repository_bytes(
    root: Path,
    name: str,
    label: str,
    *,
    max_bytes: int = MAX_SMALL_FILE_BYTES,
) -> bytes:
    fd, before = open_regular_fd(root, name, label)
    try:
        if before.st_size < 0 or before.st_size > max_bytes:
            raise ValidationError(f"{label} has an unsafe size: {name}")
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(fd, min(1 << 20, remaining))
            if not chunk:
                raise ValidationError(f"{label} changed while read: {name}")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(fd, 1):
            raise ValidationError(f"{label} changed while read: {name}")
        after = os.fstat(fd)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise ValidationError(f"{label} changed while read: {name}")
        return b"".join(chunks)
    finally:
        os.close(fd)


def read_repository_text(root: Path, name: str, label: str) -> str:
    try:
        return read_repository_bytes(root, name, label).decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise ValidationError(f"{label} is not strict UTF-8: {name}") from exc


def sha256_repository_file(root: Path, name: str, label: str) -> str:
    fd, before = open_regular_fd(root, name, label)
    try:
        if before.st_size <= 0 or before.st_size > MAX_ARCHIVE_BYTES:
            raise ValidationError(f"{label} has an unsafe size: {name}")
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(fd, 1 << 20)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_ARCHIVE_BYTES:
                raise ValidationError(f"{label} exceeds the size limit: {name}")
            digest.update(chunk)
        after = os.fstat(fd)
        if total != before.st_size or (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise ValidationError(f"{label} changed while hashed: {name}")
        return digest.hexdigest()
    finally:
        os.close(fd)


def load_json(root: Path, name: str, label: str) -> dict[str, Any]:
    try:
        value = json.loads(read_repository_text(root, name, label))
    except json.JSONDecodeError as exc:
        raise ValidationError(f"{label} is not valid JSON: {name}") from exc
    if not isinstance(value, dict):
        raise ValidationError(f"{name} must contain one JSON object")
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


def validate_filename_array(
    manifest: dict[str, Any], key: str, *, allow_empty: bool = False
) -> tuple[str, ...]:
    values = manifest.get(key)
    if not isinstance(values, list) or (not values and not allow_empty):
        requirement = "an array" if allow_empty else "a non-empty array"
        raise ValidationError(f"{key} must be {requirement}")
    normalized: list[str] = []
    for index, value in enumerate(values):
        if not isinstance(value, str) or not value:
            raise ValidationError(f"{key}[{index}] must be a non-empty string")
        validate_safe_name(value, f"{key}[{index}]")
        normalized.append(value)
    if len(normalized) != len(set(normalized)):
        raise ValidationError(f"{key} contains duplicates")
    return tuple(normalized)


def validate_manifest(
    manifest: dict[str, Any],
) -> tuple[str, str, str, str, bool, dict[str, Any], tuple[str, ...]]:
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

    source_artifacts = manifest.get("source_artifacts")
    if not isinstance(source_artifacts, dict):
        raise ValidationError("source_artifacts must be an object")
    expected_artifact_keys = {"archive", "checksum_manifest", "signature"}
    if set(source_artifacts) != expected_artifact_keys:
        raise ValidationError(
            "source_artifacts must contain exactly archive, checksum_manifest, and signature"
        )
    for key in sorted(expected_artifact_keys):
        require_text(source_artifacts, key, "source_artifacts")

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

    authoritative_documents = validate_filename_array(manifest, "authoritative_documents")
    if set(authoritative_documents) != EXPECTED_AUTHORITATIVE_DOCUMENTS:
        raise ValidationError(
            "authoritative_documents must match the reviewed maintained set: "
            + ", ".join(sorted(EXPECTED_AUTHORITATIVE_DOCUMENTS))
        )
    validate_filename_array(manifest, "historical_documents", allow_empty=True)

    return (
        version,
        signed_base,
        channel,
        status,
        publish_allowed,
        source_artifacts,
        authoritative_documents,
    )


def verify_checksum_signature(
    checksum_bytes: bytes,
    signature_bytes: bytes,
    repository_key_bytes: bytes,
    *,
    trusted_release_public_key: bytes,
) -> None:
    if repository_key_bytes != trusted_release_public_key:
        raise ValidationError("release public key does not match the embedded trust root")
    if len(signature_bytes) != 64:
        raise ValidationError("checksum signature must be the reviewed 64-byte raw signature")
    openssl = shutil.which("openssl")
    if not openssl:
        raise ValidationError("openssl is required for release signature verification")
    try:
        with tempfile.TemporaryDirectory(prefix="hostpanel-release-verify-") as directory:
            work = Path(directory)
            os.chmod(work, 0o700)
            key_path = work / "trusted-release.pub"
            checksums_path = work / "SHA256SUMS"
            signature_path = work / "SHA256SUMS.sig"
            key_path.write_bytes(trusted_release_public_key)
            checksums_path.write_bytes(checksum_bytes)
            signature_path.write_bytes(signature_bytes)
            os.chmod(key_path, 0o600)
            os.chmod(checksums_path, 0o600)
            os.chmod(signature_path, 0o600)
            result = subprocess.run(
                [
                    openssl,
                    "pkeyutl",
                    "-verify",
                    "-pubin",
                    "-inkey",
                    str(key_path),
                    "-rawin",
                    "-in",
                    str(checksums_path),
                    "-sigfile",
                    str(signature_path),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                env={"PATH": os.path.dirname(openssl)},
            )
    except OSError as exc:
        raise ValidationError("cannot execute release signature verification") from exc
    if result.returncode != 0:
        raise ValidationError("checksum manifest signature verification failed")


def validate_source_artifacts(
    root: Path,
    source_artifacts: dict[str, Any],
    signed_base: str,
    *,
    trusted_release_public_key: bytes = TRUSTED_RELEASE_PUBLIC_KEY,
) -> tuple[str, str]:
    archive_name = require_text(source_artifacts, "archive", "source_artifacts")
    checksum_name = require_text(source_artifacts, "checksum_manifest", "source_artifacts")
    signature_name = require_text(source_artifacts, "signature", "source_artifacts")

    expected_archive = f"hostpanel-v{signed_base}-source.tar.gz"
    if archive_name != expected_archive:
        raise ValidationError(
            "source_artifacts.archive must match release.signed_base: "
            f"expected {expected_archive}, got {archive_name}"
        )
    expected_signature = "SHA256SUMS.sig"
    if signature_name != expected_signature:
        raise ValidationError(f"source_artifacts.signature must be {expected_signature}")

    checksum_bytes = read_repository_bytes(root, checksum_name, "checksum manifest")
    signature_bytes = read_repository_bytes(root, signature_name, "checksum signature")
    release_key_name = f"hostpanel-v{signed_base}-release.pub"
    release_key_bytes = read_repository_bytes(root, release_key_name, "release public key")
    verify_checksum_signature(
        checksum_bytes,
        signature_bytes,
        release_key_bytes,
        trusted_release_public_key=trusted_release_public_key,
    )

    try:
        lines = checksum_bytes.decode("utf-8", errors="strict").splitlines()
    except UnicodeError as exc:
        raise ValidationError("checksum manifest is not strict UTF-8") from exc

    matches: list[str] = []
    source_tarballs: list[str] = []
    for index, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            raise ValidationError(f"checksum manifest line {index} is malformed")
        digest, filename = parts
        filename = filename.lstrip("*")
        if not DIGEST_RE.fullmatch(digest):
            raise ValidationError(f"checksum manifest line {index} has an invalid SHA-256")
        validate_safe_name(filename, f"checksum manifest line {index} filename")
        if SOURCE_TARBALL_RE.fullmatch(filename):
            source_tarballs.append(filename)
        if filename == archive_name:
            matches.append(digest)

    if source_tarballs != [archive_name]:
        raise ValidationError(
            "checksum manifest must identify exactly one HostPanel source tarball "
            f"and it must be {archive_name}"
        )
    if len(matches) != 1:
        raise ValidationError(
            f"checksum manifest must contain exactly one entry for {archive_name}; "
            f"found {len(matches)}"
        )

    actual = sha256_repository_file(root, archive_name, "signed source archive")
    if actual != matches[0]:
        raise ValidationError(
            f"signed source archive checksum mismatch: expected {matches[0]}, got {actual}"
        )
    return archive_name, actual


def validate_version_file(root: Path, version: str) -> None:
    value = read_repository_text(root, "RELEASE_VERSION", "release version file").strip()
    if value != version:
        raise ValidationError("RELEASE_VERSION does not match release.version")


def require_visible(text: str, name: str, needle: str, label: str) -> None:
    if needle not in text:
        raise ValidationError(f"{name} has a conflicting or missing visible {label} declaration")


def validate_readme_platform(text: str, platform: dict[str, Any]) -> None:
    for operating_system in platform["operating_systems"]:
        require_visible(text, "README.md", operating_system, f"platform OS {operating_system}")
    architecture_tokens = {
        "x86_64": ("x86-64", "AMD64"),
        "aarch64": ("ARM64", "AArch64"),
    }
    for architecture in platform["architectures"]:
        tokens = architecture_tokens.get(architecture, (architecture,))
        if not any(token in text for token in tokens):
            raise ValidationError(
                f"README.md has a conflicting or missing visible architecture declaration: {architecture}"
            )
    ram = platform["minimum_ram_mib"]
    root_free = platform["minimum_root_free_mib"]
    ram_text = f"{ram // 1024} GiB RAM" if ram % 1024 == 0 else f"{ram} MiB RAM"
    root_text = f"{root_free // 1024} GiB free" if root_free % 1024 == 0 else f"{root_free} MiB free"
    require_visible(text, "README.md", ram_text, "minimum RAM")
    require_visible(text, "README.md", root_text, "minimum root free space")


def validate_docs(
    root: Path,
    authoritative_documents: tuple[str, ...],
    version: str,
    signed_base: str,
    channel: str,
    status: str,
    publish_allowed: bool,
    platform: dict[str, Any],
) -> None:
    required_tokens = {
        "{{HOSTPANEL_RELEASE_VERSION}}": version,
        "{{HOSTPANEL_SIGNED_BASE}}": signed_base,
        "{{HOSTPANEL_RELEASE_STATUS}}": status,
        "{{HOSTPANEL_PUBLICATION_ALLOWED}}": str(publish_allowed).lower(),
    }
    markdown_docs = [name for name in authoritative_documents if name.endswith(".md")]
    for name in markdown_docs:
        text = read_repository_text(root, name, "authoritative document")
        for marker, expected in required_tokens.items():
            declaration = f"{marker}={expected}"
            if declaration not in text:
                raise ValidationError(f"{name} is missing authoritative marker {declaration}")

        stale_installed = re.findall(
            r"(?:installed(?: application)? version|/opt/hostpanel/VERSION)"
            r"[^0-9]{0,80}([0-9]+\.[0-9]+\.[0-9]+)",
            text,
            flags=re.IGNORECASE,
        )
        conflicting = sorted({found for found in stale_installed if found != version})
        if conflicting:
            raise ValidationError(
                f"{name} contains conflicting installed version(s): {', '.join(conflicting)}"
            )

        if name == "README.md":
            require_visible(text, name, f"Current deployable release: **{version}**", "release version")
            require_visible(text, name, f"Authenticated signed base: **{signed_base}**", "signed base")
            require_visible(text, name, f"Release channel: **{channel}**", "release channel")
            publication = "allowed" if publish_allowed else "blocked"
            require_visible(text, name, f"Production publication: **{publication}**", "publication")
            if status == "deployable-not-publishable":
                require_visible(
                    text,
                    name,
                    "A deployable build is not the same as an approved production publication.",
                    "release status",
                )
            validate_readme_platform(text, platform)
        elif name == "CONFIGURATION.md":
            pattern = re.compile(
                rf"HostPanel `{re.escape(version)}`,\s*derived from signed base \*\*{re.escape(signed_base)}\*\*",
                flags=re.IGNORECASE,
            )
            if not pattern.search(text):
                raise ValidationError(f"{name} has conflicting visible release/base declarations")
        elif name == "PRODUCTION_READINESS.md":
            pattern = re.compile(
                rf"deployable HostPanel release \*\*{re.escape(version)}\*\*,\s*derived from\s*signed base \*\*{re.escape(signed_base)}\*\*",
                flags=re.IGNORECASE,
            )
            if not pattern.search(text):
                raise ValidationError(f"{name} has conflicting visible release/base declarations")
            if not publish_allowed:
                require_visible(text, name, "It does not itself authorize production", "publication")


def validate_repository(
    root: Path,
    *,
    trusted_release_public_key: bytes = TRUSTED_RELEASE_PUBLIC_KEY,
) -> tuple[str, str, str, bool, str, str]:
    root = root.resolve()
    manifest = load_json(root, "RELEASE-MANIFEST.json", "release manifest")
    (
        version,
        signed_base,
        channel,
        status,
        publish_allowed,
        source_artifacts,
        authoritative_documents,
    ) = validate_manifest(manifest)
    platform = manifest["platform"]
    archive_name, archive_digest = validate_source_artifacts(
        root,
        source_artifacts,
        signed_base,
        trusted_release_public_key=trusted_release_public_key,
    )
    validate_version_file(root, version)
    validate_docs(
        root,
        authoritative_documents,
        version,
        signed_base,
        channel,
        status,
        publish_allowed,
        platform,
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
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="repository root to validate")
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
        f"version={version} signed_base={signed_base} status={status} "
        f"production_publish_allowed={str(publish_allowed).lower()} "
        f"archive={archive_name} sha256={archive_digest}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
