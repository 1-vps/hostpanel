#!/usr/bin/env python3
"""Generate and verify deterministic source-release attestations."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import shutil
import stat
import tarfile
from dataclasses import dataclass
from typing import Iterable, Sequence
from urllib.parse import quote


SBOM_NAME = "sbom.cdx.json"
LOCK_ATTESTATION_NAME = "dependency-lock-attestation.json"
FILE_MANIFEST_NAME = "source-file-manifest.json"
ATTESTATION_NAMES = (SBOM_NAME, LOCK_ATTESTATION_NAME, FILE_MANIFEST_NAME)
MAX_JSON_BYTES = 32 * 1024 * 1024
MAX_LOCK_BYTES = 16 * 1024 * 1024
PACKAGE_RE = re.compile(
    r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]+\])?==([^\s;]+)"
    r"(?:\s*;\s*(.*?))?(?=\s+--hash=|$)"
)
HASH_RE = re.compile(r"--hash=sha256:([0-9a-f]{64})(?=\s|$)")


class AttestationError(RuntimeError):
    """Raised when source attestations cannot be generated or verified safely."""


@dataclass(frozen=True)
class LockedPackage:
    name: str
    version: str
    marker: str | None
    hashes: tuple[str, ...]

    @property
    def purl(self) -> str:
        return f"pkg:pypi/{quote(self.name, safe='-._~')}@{quote(self.version, safe='-._~')}"

    def lock_record(self) -> dict[str, object]:
        return {
            "hashes": list(self.hashes),
            "marker": self.marker,
            "name": self.name,
            "version": self.version,
        }

    def sbom_component(self) -> dict[str, object]:
        component: dict[str, object] = {
            "bom-ref": self.purl,
            "name": self.name,
            "purl": self.purl,
            "type": "library",
            "version": self.version,
        }
        if self.marker is not None:
            component["properties"] = [
                {"name": "hostpanel:environment-marker", "value": self.marker}
            ]
        return component


def canonical_json(payload: object) -> bytes:
    return (
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_package_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def read_regular_file(path: pathlib.Path, *, maximum_size: int) -> bytes:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise AttestationError(f"required attestation input is missing: {path}") from exc
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise AttestationError(f"attestation input must be a single-linked regular file: {path}")
    if metadata.st_size <= 0 or metadata.st_size > maximum_size:
        raise AttestationError(f"attestation input has an unsafe size: {path}")
    data = path.read_bytes()
    after = path.lstat()
    if (
        metadata.st_dev != after.st_dev
        or metadata.st_ino != after.st_ino
        or metadata.st_size != after.st_size
        or metadata.st_mtime_ns != after.st_mtime_ns
    ):
        raise AttestationError(f"attestation input changed while reading: {path}")
    return data


def logical_requirement_lines(text: str) -> list[str]:
    entries: list[str] = []
    current = ""
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        continued = stripped.endswith("\\")
        if continued:
            stripped = stripped[:-1].rstrip()
        current = f"{current} {stripped}".strip()
        if not continued:
            entries.append(current)
            current = ""
    if current:
        raise AttestationError("requirements.lock ends with an unterminated continuation")
    return entries


def parse_requirements_lock(data: bytes) -> tuple[LockedPackage, ...]:
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise AttestationError("requirements.lock is not valid UTF-8") from exc
    packages: list[LockedPackage] = []
    seen: set[str] = set()
    for entry in logical_requirement_lines(text):
        match = PACKAGE_RE.match(entry)
        if match is None:
            raise AttestationError(f"requirements.lock contains an unsupported entry: {entry[:80]!r}")
        name = normalize_package_name(match.group(1))
        version = match.group(2)
        marker = match.group(3).strip() if match.group(3) else None
        hashes = tuple(sorted(set(HASH_RE.findall(entry))))
        if not hashes:
            raise AttestationError(f"locked package has no SHA-256 artifacts: {name}")
        if name in seen:
            raise AttestationError(f"requirements.lock contains a duplicate normalized package: {name}")
        seen.add(name)
        packages.append(
            LockedPackage(name=name, version=version, marker=marker, hashes=hashes)
        )
    if not packages:
        raise AttestationError("requirements.lock contains no packages")
    packages.sort(key=lambda package: (package.name, package.version, package.marker or ""))
    return tuple(packages)


def utc_timestamp(timestamp: int) -> str:
    return (
        dt.datetime.fromtimestamp(timestamp, tz=dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def build_lock_attestation(
    *,
    lock_data: bytes,
    packages: Sequence[LockedPackage],
    source_version: str,
    release_id: str,
    timestamp: int,
) -> dict[str, object]:
    return {
        "generated_at": utc_timestamp(timestamp),
        "lock": {
            "format": "uv-pip-compile-sha256",
            "package_count": len(packages),
            "path": "requirements.lock",
            "sha256": sha256_bytes(lock_data),
        },
        "packages": [package.lock_record() for package in packages],
        "product": "hostpanel",
        "release_id": release_id,
        "schema": 1,
        "source_version": source_version,
    }


def build_sbom(
    *,
    lock_data: bytes,
    packages: Sequence[LockedPackage],
    source_version: str,
    release_id: str,
    timestamp: int,
) -> dict[str, object]:
    return {
        "bomFormat": "CycloneDX",
        "components": [package.sbom_component() for package in packages],
        "metadata": {
            "component": {
                "bom-ref": f"pkg:generic/hostpanel@{quote(source_version, safe='-._~')}",
                "name": "hostpanel",
                "properties": [
                    {"name": "hostpanel:release-id", "value": release_id},
                    {
                        "name": "hostpanel:requirements-lock-sha256",
                        "value": sha256_bytes(lock_data),
                    },
                ],
                "type": "application",
                "version": source_version,
            },
            "timestamp": utc_timestamp(timestamp),
            "tools": {
                "components": [
                    {
                        "name": "generate-source-attestations.py",
                        "type": "application",
                        "version": "1",
                    }
                ]
            },
        },
        "specVersion": "1.6",
        "version": 1,
    }


def write_exclusive(path: pathlib.Path, data: bytes) -> None:
    if path.exists() or path.is_symlink():
        raise AttestationError(f"refusing to overwrite attestation output: {path}")
    with path.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(path, 0o644)


def relative_source_entries(
    source_root: pathlib.Path,
    *,
    excluded: frozenset[str] = frozenset(),
) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for path in sorted(
        source_root.rglob("*"),
        key=lambda candidate: candidate.relative_to(source_root).as_posix(),
    ):
        relative = path.relative_to(source_root).as_posix()
        if relative in excluded:
            continue
        metadata = path.lstat()
        mode = f"{stat.S_IMODE(metadata.st_mode):04o}"
        if stat.S_ISDIR(metadata.st_mode):
            entries.append(
                {
                    "mode": mode,
                    "path": relative,
                    "sha256": None,
                    "size": 0,
                    "type": "directory",
                }
            )
        elif stat.S_ISREG(metadata.st_mode):
            if metadata.st_nlink != 1:
                raise AttestationError(f"source file has multiple hard links: {relative}")
            entries.append(
                {
                    "mode": mode,
                    "path": relative,
                    "sha256": sha256_file(path),
                    "size": metadata.st_size,
                    "type": "file",
                }
            )
        else:
            raise AttestationError(f"source tree contains an unsupported entry: {relative}")
    return entries


def build_file_manifest(
    source_root: pathlib.Path,
    *,
    source_version: str,
    release_id: str,
) -> dict[str, object]:
    return {
        "entries": relative_source_entries(
            source_root,
            excluded=frozenset({FILE_MANIFEST_NAME}),
        ),
        "product": "hostpanel",
        "release_id": release_id,
        "schema": 1,
        "scope": {
            "excludes": [FILE_MANIFEST_NAME],
            "root": ".",
        },
        "source_version": source_version,
    }


def generate_attestations(
    source_root: pathlib.Path,
    *,
    source_version: str,
    release_id: str,
    timestamp: int,
) -> None:
    lock_path = source_root / "requirements.lock"
    lock_data = read_regular_file(lock_path, maximum_size=MAX_LOCK_BYTES)
    packages = parse_requirements_lock(lock_data)
    write_exclusive(
        source_root / LOCK_ATTESTATION_NAME,
        canonical_json(
            build_lock_attestation(
                lock_data=lock_data,
                packages=packages,
                source_version=source_version,
                release_id=release_id,
                timestamp=timestamp,
            )
        ),
    )
    write_exclusive(
        source_root / SBOM_NAME,
        canonical_json(
            build_sbom(
                lock_data=lock_data,
                packages=packages,
                source_version=source_version,
                release_id=release_id,
                timestamp=timestamp,
            )
        ),
    )
    write_exclusive(
        source_root / FILE_MANIFEST_NAME,
        canonical_json(
            build_file_manifest(
                source_root,
                source_version=source_version,
                release_id=release_id,
            )
        ),
    )
    verify_tree_attestations(
        source_root,
        source_version=source_version,
        release_id=release_id,
        timestamp=timestamp,
    )


def load_json_file(path: pathlib.Path) -> object:
    data = read_regular_file(path, maximum_size=MAX_JSON_BYTES)
    try:
        return json.loads(data.decode("utf-8", errors="strict"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise AttestationError(f"invalid JSON attestation: {path}") from exc


def verify_tree_attestations(
    source_root: pathlib.Path,
    *,
    source_version: str,
    release_id: str,
    timestamp: int,
) -> None:
    lock_data = read_regular_file(source_root / "requirements.lock", maximum_size=MAX_LOCK_BYTES)
    packages = parse_requirements_lock(lock_data)
    expected_lock = build_lock_attestation(
        lock_data=lock_data,
        packages=packages,
        source_version=source_version,
        release_id=release_id,
        timestamp=timestamp,
    )
    expected_sbom = build_sbom(
        lock_data=lock_data,
        packages=packages,
        source_version=source_version,
        release_id=release_id,
        timestamp=timestamp,
    )
    expected_manifest = build_file_manifest(
        source_root,
        source_version=source_version,
        release_id=release_id,
    )
    if load_json_file(source_root / LOCK_ATTESTATION_NAME) != expected_lock:
        raise AttestationError("dependency lock attestation does not match requirements.lock")
    if load_json_file(source_root / SBOM_NAME) != expected_sbom:
        raise AttestationError("SBOM does not match requirements.lock")
    if load_json_file(source_root / FILE_MANIFEST_NAME) != expected_manifest:
        raise AttestationError("source file manifest does not match the source tree")


def safe_tar_json(archive: tarfile.TarFile, member: tarfile.TarInfo) -> object:
    if not member.isfile() or member.size <= 0 or member.size > MAX_JSON_BYTES:
        raise AttestationError(f"archive attestation has an unsafe type or size: {member.name}")
    handle = archive.extractfile(member)
    if handle is None:
        raise AttestationError(f"could not read archive attestation: {member.name}")
    with handle:
        data = handle.read(MAX_JSON_BYTES + 1)
    if len(data) != member.size or len(data) > MAX_JSON_BYTES:
        raise AttestationError(f"archive attestation read was incomplete or oversized: {member.name}")
    try:
        return json.loads(data.decode("utf-8", errors="strict"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise AttestationError(f"archive attestation is invalid JSON: {member.name}") from exc


def archive_inventory(
    archive: tarfile.TarFile,
    root_name: str,
) -> list[dict[str, object]]:
    prefix = f"{root_name}/"
    records: list[dict[str, object]] = []
    for member in archive.getmembers():
        normalized = member.name.rstrip("/")
        if normalized == root_name:
            continue
        if not normalized.startswith(prefix):
            raise AttestationError(f"archive member is outside the expected root: {member.name}")
        relative = normalized[len(prefix) :]
        if relative == FILE_MANIFEST_NAME:
            continue
        mode = f"{stat.S_IMODE(member.mode):04o}"
        if member.isdir():
            records.append(
                {
                    "mode": mode,
                    "path": relative,
                    "sha256": None,
                    "size": 0,
                    "type": "directory",
                }
            )
        elif member.isfile():
            handle = archive.extractfile(member)
            if handle is None:
                raise AttestationError(f"could not read archive member: {member.name}")
            digest = hashlib.sha256()
            total = 0
            with handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
                    total += len(chunk)
            if total != member.size:
                raise AttestationError(f"archive member changed size while reading: {member.name}")
            records.append(
                {
                    "mode": mode,
                    "path": relative,
                    "sha256": digest.hexdigest(),
                    "size": member.size,
                    "type": "file",
                }
            )
        else:
            raise AttestationError(f"archive contains an unsupported member: {member.name}")
    records.sort(key=lambda record: str(record["path"]))
    return records


def verify_archive_attestations(
    archive_path: pathlib.Path,
    *,
    root_name: str,
    source_version: str,
    release_id: str,
) -> None:
    with tarfile.open(archive_path, "r:gz") as archive:
        members_by_name = {member.name.rstrip("/"): member for member in archive.getmembers()}
        required = {
            name: f"{root_name}/{name}"
            for name in ATTESTATION_NAMES
        }
        payloads = {
            name: safe_tar_json(archive, members_by_name[path])
            for name, path in required.items()
            if path in members_by_name
        }
        if set(payloads) != set(ATTESTATION_NAMES):
            missing = sorted(set(ATTESTATION_NAMES) - set(payloads))
            raise AttestationError(f"archive is missing source attestations: {missing}")
        manifest = payloads[FILE_MANIFEST_NAME]
        if not isinstance(manifest, dict):
            raise AttestationError("archive source file manifest is not an object")
        if manifest.get("source_version") != source_version or manifest.get("release_id") != release_id:
            raise AttestationError("archive source file manifest identity mismatch")
        if manifest.get("entries") != archive_inventory(archive, root_name):
            raise AttestationError("archive source file manifest does not match archive members")
        sbom = payloads[SBOM_NAME]
        if not isinstance(sbom, dict) or sbom.get("bomFormat") != "CycloneDX" or sbom.get("specVersion") != "1.6":
            raise AttestationError("archive SBOM identity mismatch")
        lock_attestation = payloads[LOCK_ATTESTATION_NAME]
        if not isinstance(lock_attestation, dict):
            raise AttestationError("archive dependency lock attestation is not an object")
        if lock_attestation.get("source_version") != source_version or lock_attestation.get("release_id") != release_id:
            raise AttestationError("archive dependency lock attestation identity mismatch")


def copy_attestations(source_root: pathlib.Path, output_dir: pathlib.Path) -> None:
    for name in ATTESTATION_NAMES:
        source = source_root / name
        destination = output_dir / name
        if destination.exists() or destination.is_symlink():
            raise AttestationError(f"refusing to overwrite exported attestation: {destination}")
        with source.open("rb") as input_handle, destination.open("xb") as output_handle:
            shutil.copyfileobj(input_handle, output_handle, length=1024 * 1024)
            output_handle.flush()
            os.fsync(output_handle.fileno())
        os.chmod(destination, 0o644)
        if sha256_file(source) != sha256_file(destination):
            raise AttestationError(f"exported attestation digest mismatch: {name}")
