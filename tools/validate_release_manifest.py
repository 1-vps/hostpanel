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


def load_json(root: Path, name: str, label: str) -> dict[str, Any]:
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValidationError(f"{label} contains duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        value = json.loads(
            read_repository_text(root, name, label),
            object_pairs_hook=reject_duplicate_keys,
        )
    except json.JSONDecodeError as exc:
        raise ValidationError(f"{label} is not valid JSON: {name}") from exc
    if not isinstance(value, dict):
        raise ValidationError(f"{name} must contain one JSON object")
    return value


def require_exact_keys(mapping: dict[str, Any], expected: set[str], context: str) -> None:
    actual = set(mapping)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if unknown:
            details.append("unknown " + ", ".join(unknown))
        raise ValidationError(f"{context} has invalid keys ({'; '.join(details)})")


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
    require_exact_keys(
        manifest,
        {
            "schema_version",
            "product",
            "release",
            "source_artifacts",
            "platform",
            "authoritative_documents",
            "historical_documents",
        },
        "manifest",
    )
    if manifest.get("schema_version") != 1:
        raise ValidationError("schema_version must be 1")
    if manifest.get("product") != "HostPanel":
        raise ValidationError("product must be HostPanel")

    release = manifest.get("release")
    if not isinstance(release, dict):
        raise ValidationError("release must be an object")
    require_exact_keys(
        release,
        {
            "version",
            "channel",
            "status",
            "signed_base",
            "production_publish_allowed",
            "blockers",
        },
        "release",
    )

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
        require_exact_keys(
            blocker,
            {"id", "description"},
            f"release.blockers[{index}]",
        )
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
    expected_artifact_keys = {
        "archive",
        "archive_signature",
        "checksum_manifest",
        "checksum_signature",
    }
    require_exact_keys(source_artifacts, expected_artifact_keys, "source_artifacts")
    for key in sorted(expected_artifact_keys):
        require_text(source_artifacts, key, "source_artifacts")

    platform = manifest.get("platform")
    if not isinstance(platform, dict):
        raise ValidationError("platform must be an object")
    require_exact_keys(
        platform,
        {
            "architectures",
            "operating_systems",
            "minimum_ram_mib",
            "minimum_root_free_mib",
        },
        "platform",
    )
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
    historical_documents = validate_filename_array(
        manifest, "historical_documents", allow_empty=True
    )
    overlap = sorted(set(authoritative_documents) & set(historical_documents))
    if overlap:
        raise ValidationError(
            "authoritative_documents and historical_documents overlap: "
            + ", ".join(overlap)
        )

    return (
        version,
        signed_base,
        channel,
        status,
        publish_allowed,
        source_artifacts,
        authoritative_documents,
    )


def verify_bytes_signature(
    payload_bytes: bytes,
    signature_bytes: bytes,
    repository_key_bytes: bytes,
    *,
    trusted_release_public_key: bytes,
    label: str,
) -> None:
    if repository_key_bytes != trusted_release_public_key:
        raise ValidationError("release public key does not match the embedded trust root")
    if len(signature_bytes) != 64:
        raise ValidationError(f"{label} must be the reviewed 64-byte raw signature")
    openssl = shutil.which("openssl")
    if not openssl:
        raise ValidationError("openssl is required for release signature verification")
    try:
        with tempfile.TemporaryDirectory(prefix="hostpanel-release-verify-") as directory:
            work = Path(directory)
            os.chmod(work, 0o700)
            key_path = work / "trusted-release.pub"
            payload_path = work / "payload"
            signature_path = work / "payload.sig"
            key_path.write_bytes(trusted_release_public_key)
            payload_path.write_bytes(payload_bytes)
            signature_path.write_bytes(signature_bytes)
            os.chmod(key_path, 0o600)
            os.chmod(payload_path, 0o600)
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
                    str(payload_path),
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
        raise ValidationError(f"{label} verification failed")


def verify_archive_signature_and_sha256(
    root: Path,
    archive_name: str,
    signature_bytes: bytes,
    trusted_release_public_key: bytes,
) -> str:
    if len(signature_bytes) != 64:
        raise ValidationError(
            "archive signature must be the reviewed 64-byte raw signature"
        )
    openssl = shutil.which("openssl")
    if not openssl:
        raise ValidationError("openssl is required for release signature verification")

    fd, before = open_regular_fd(root, archive_name, "signed source archive")
    try:
        if before.st_size <= 0 or before.st_size > MAX_ARCHIVE_BYTES:
            raise ValidationError(
                f"signed source archive has an unsafe size: {archive_name}"
            )
        try:
            with tempfile.TemporaryDirectory(
                prefix="hostpanel-archive-verify-"
            ) as directory:
                work = Path(directory)
                os.chmod(work, 0o700)
                key_path = work / "trusted-release.pub"
                signature_path = work / "archive.sig"
                key_path.write_bytes(trusted_release_public_key)
                signature_path.write_bytes(signature_bytes)
                os.chmod(key_path, 0o600)
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
                        f"/proc/self/fd/{fd}",
                        "-sigfile",
                        str(signature_path),
                    ],
                    pass_fds=(fd,),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    env={"PATH": os.path.dirname(openssl)},
                )
        except OSError as exc:
            raise ValidationError("cannot execute archive signature verification") from exc
        if result.returncode != 0:
            raise ValidationError("archive signature verification failed")

        os.lseek(fd, 0, os.SEEK_SET)
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(fd, 1 << 20)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_ARCHIVE_BYTES:
                raise ValidationError("signed source archive exceeds the size limit")
            digest.update(chunk)
        after = os.fstat(fd)
        before_identity = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        after_identity = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if total != before.st_size or before_identity != after_identity:
            raise ValidationError(
                f"signed source archive changed while verified: {archive_name}"
            )
        return digest.hexdigest()
    finally:
        os.close(fd)


def validate_source_artifacts(
    root: Path,
    source_artifacts: dict[str, Any],
    signed_base: str,
    *,
    trusted_release_public_key: bytes = TRUSTED_RELEASE_PUBLIC_KEY,
) -> tuple[str, str]:
    archive_name = require_text(source_artifacts, "archive", "source_artifacts")
    checksum_name = require_text(source_artifacts, "checksum_manifest", "source_artifacts")
    archive_signature_name = require_text(
        source_artifacts, "archive_signature", "source_artifacts"
    )
    checksum_signature_name = require_text(
        source_artifacts, "checksum_signature", "source_artifacts"
    )
    for artifact_name, label in (
        (archive_name, "source_artifacts.archive"),
        (archive_signature_name, "source_artifacts.archive_signature"),
        (checksum_name, "source_artifacts.checksum_manifest"),
        (checksum_signature_name, "source_artifacts.checksum_signature"),
    ):
        validate_safe_name(artifact_name, label)

    expected_archive = f"hostpanel-v{signed_base}-source.tar.gz"
    if archive_name != expected_archive:
        raise ValidationError(
            "source_artifacts.archive must match release.signed_base: "
            f"expected {expected_archive}, got {archive_name}"
        )
    if archive_signature_name != f"{expected_archive}.sig":
        raise ValidationError(
            "source_artifacts.archive_signature must be the installer-required "
            f"{expected_archive}.sig"
        )
    if checksum_name != "SHA256SUMS":
        raise ValidationError("source_artifacts.checksum_manifest must be SHA256SUMS")
    if checksum_signature_name != "SHA256SUMS.sig":
        raise ValidationError(
            "source_artifacts.checksum_signature must be SHA256SUMS.sig"
        )

    checksum_bytes = read_repository_bytes(root, checksum_name, "checksum manifest")
    checksum_signature_bytes = read_repository_bytes(
        root, checksum_signature_name, "checksum signature"
    )
    archive_signature_bytes = read_repository_bytes(
        root, archive_signature_name, "archive signature"
    )
    release_key_name = f"hostpanel-v{signed_base}-release.pub"
    release_key_bytes = read_repository_bytes(root, release_key_name, "release public key")
    verify_bytes_signature(
        checksum_bytes,
        checksum_signature_bytes,
        release_key_bytes,
        trusted_release_public_key=trusted_release_public_key,
        label="checksum manifest signature",
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

    actual = verify_archive_signature_and_sha256(
        root,
        archive_name,
        archive_signature_bytes,
        trusted_release_public_key,
    )
    if actual != matches[0]:
        raise ValidationError(
            f"signed source archive checksum mismatch: expected {matches[0]}, got {actual}"
        )
    return archive_name, actual


def validate_version_file(root: Path, version: str) -> None:
    value = read_repository_text(root, "RELEASE_VERSION", "release version file").strip()
    if value != version:
        raise ValidationError("RELEASE_VERSION does not match release.version")


def markdown_visible_text(text: str) -> str:
    without_comments = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    return re.sub(
        r"(?ms)^[ \t]*(?:```|~~~).*?^[ \t]*(?:```|~~~)[ \t]*$",
        "",
        without_comments,
    )


def require_unique_visible_line(
    text: str,
    name: str,
    pattern: re.Pattern[str],
    expected: str,
    label: str,
) -> None:
    if pattern.findall(text) != [expected]:
        raise ValidationError(
            f"{name} has a conflicting, duplicate, or missing visible {label} declaration"
        )


def require_unique_visible_text(text: str, name: str, needle: str, label: str) -> None:
    if text.count(needle) != 1:
        raise ValidationError(
            f"{name} has a conflicting, duplicate, or missing visible {label} declaration"
        )


def validate_readme_platform(text: str, platform: dict[str, Any]) -> None:
    distro_names = ("Ubuntu", "Debian", "Rocky Linux", "AlmaLinux")
    expected_by_distro: dict[str, list[str]] = {name: [] for name in distro_names}
    for operating_system in platform["operating_systems"]:
        for distro in distro_names:
            prefix = f"{distro} "
            if operating_system.startswith(prefix):
                expected_by_distro[distro].append(operating_system[len(prefix):])
                break
        else:
            raise ValidationError(
                f"README.md has no reviewed visible OS mapping for {operating_system}"
            )

    expected_os_lines: set[str] = set()
    for distro, expected_versions in expected_by_distro.items():
        if not expected_versions:
            continue
        if len(expected_versions) == 1:
            versions = expected_versions[0]
        elif len(expected_versions) == 2:
            versions = " or ".join(expected_versions)
        else:
            versions = ", ".join(expected_versions[:-1]) + f", or {expected_versions[-1]}"
        expected_os_lines.add(f"- {distro} {versions}")

    visible_os_lines = {
        match.group(0)
        for match in re.finditer(
            r"(?m)^- [A-Z][A-Za-z ]* [0-9]+(?:\.[0-9]+)?"
            r"(?:,? (?:or )?[0-9]+(?:\.[0-9]+)?)*$",
            text,
        )
    }
    if visible_os_lines != expected_os_lines:
        raise ValidationError("README.md visible operating-system list does not match the manifest")

    architecture_tokens = {
        "x86_64": "x86-64/AMD64",
        "aarch64": "ARM64/AArch64",
    }
    try:
        architecture_display = " or ".join(
            architecture_tokens[item] for item in platform["architectures"]
        )
    except KeyError as exc:
        raise ValidationError(
            f"README.md has no reviewed visible architecture mapping for {exc.args[0]}"
        ) from exc
    require_unique_visible_line(
        text,
        "README.md",
        re.compile(r"(?m)^- (?P<value>.*(?:AMD64|AArch64).*)$"),
        architecture_display,
        "architecture list",
    )

    ram = platform["minimum_ram_mib"]
    root_free = platform["minimum_root_free_mib"]
    ram_text = f"{ram // 1024} GiB" if ram % 1024 == 0 else f"{ram} MiB"
    root_text = f"{root_free // 1024} GiB" if root_free % 1024 == 0 else f"{root_free} MiB"
    require_unique_visible_line(
        text,
        "README.md",
        re.compile(r"(?m)^- (?P<value>at least .* RAM and .* free on `/`)$"),
        f"at least {ram_text} RAM and {root_text} free on `/`",
        "resource minimums",
    )


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
            values = re.findall(rf"{re.escape(marker)}=([^\s<]+)", text)
            if values != [expected]:
                raise ValidationError(
                    f"{name} has a conflicting, duplicate, or missing authoritative marker "
                    f"{marker}={expected}"
                )

        visible = markdown_visible_text(text)

        stale_installed = re.findall(
            r"(?:installed(?: application)? version|/opt/hostpanel/VERSION)"
            r"[^0-9]{0,80}([0-9]+\.[0-9]+\.[0-9]+)",
            re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL),
            flags=re.IGNORECASE,
        )
        conflicting = sorted({found for found in stale_installed if found != version})
        if conflicting:
            raise ValidationError(
                f"{name} contains conflicting installed version(s): {', '.join(conflicting)}"
            )

        if name == "README.md":
            require_unique_visible_line(
                visible,
                name,
                re.compile(
                    r"(?m)^- Current deployable release: \*\*(?P<value>[^*]+)\*\*$"
                ),
                version,
                "release version",
            )
            require_unique_visible_line(
                visible,
                name,
                re.compile(
                    r"(?m)^- Authenticated signed base: \*\*(?P<value>[^*]+)\*\*$"
                ),
                signed_base,
                "signed base",
            )
            require_unique_visible_line(
                visible,
                name,
                re.compile(r"(?m)^- Release channel: \*\*(?P<value>[^*]+)\*\*$"),
                channel,
                "release channel",
            )
            publication = "allowed" if publish_allowed else "blocked"
            require_unique_visible_line(
                visible,
                name,
                re.compile(
                    r"(?m)^- Production publication: \*\*(?P<value>[^*]+)\*\*$"
                ),
                publication,
                "publication",
            )
            if status != "deployable-not-publishable":
                raise ValidationError(
                    "README.md visible release-status contract must be updated before "
                    f"using manifest status {status}"
                )
            require_unique_visible_text(
                visible,
                name,
                "A deployable build is not the same as an approved production publication.",
                "release status",
            )
            validate_readme_platform(visible, platform)
        elif name == "CONFIGURATION.md":
            pattern = re.compile(
                r"HostPanel `(?P<version>[0-9]+\.[0-9]+\.[0-9]+)`,\s*"
                r"derived from signed base \*\*(?P<base>[0-9.]+-hardened-r[0-9]+)\*\*",
                flags=re.IGNORECASE,
            )
            if pattern.findall(visible) != [(version, signed_base)]:
                raise ValidationError(f"{name} has conflicting visible release/base declarations")
        elif name == "PRODUCTION_READINESS.md":
            pattern = re.compile(
                r"deployable HostPanel release \*\*(?P<version>[0-9]+\.[0-9]+\.[0-9]+)\*\*,\s*"
                r"derived from\s*signed base \*\*(?P<base>[0-9.]+-hardened-r[0-9]+)\*\*",
                flags=re.IGNORECASE,
            )
            if pattern.findall(visible) != [(version, signed_base)]:
                raise ValidationError(f"{name} has conflicting visible release/base declarations")
            if not publish_allowed:
                require_unique_visible_text(
                    visible,
                    name,
                    "It does not itself authorize production",
                    "publication",
                )


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
