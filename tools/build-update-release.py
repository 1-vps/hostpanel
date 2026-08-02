#!/usr/bin/env python3
"""Build a deterministic HostPanel update archive and canonical manifest."""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import json
import os
import pathlib
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass

SEMVER_RE = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class ReleaseIdentity:
    source_version: str
    release_id: str


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_git(root: pathlib.Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return completed.stdout.strip()


def read_single_line(path: pathlib.Path, label: str) -> str:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise SystemExit(f"{label} is missing: {path}") from exc
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise SystemExit(f"{label} must be a single-linked regular file")
    raw = path.read_text(encoding="utf-8", errors="strict")
    lines = raw.splitlines()
    if (
        len(lines) != 1
        or raw not in {lines[0], lines[0] + "\n"}
        or not lines[0]
        or lines[0].strip() != lines[0]
    ):
        raise SystemExit(f"{label} must contain exactly one clean non-empty line")
    return lines[0]


def semver_core(value: str, label: str) -> str:
    match = SEMVER_RE.fullmatch(value)
    if match is None:
        raise SystemExit(f"{label} is not semantic: {value!r}")
    return ".".join(match.group(index) for index in (1, 2, 3))


def release_identity(root: pathlib.Path) -> ReleaseIdentity:
    source_version = read_single_line(root / "SOURCE_VERSION", "SOURCE_VERSION")
    release_id = read_single_line(root / "RELEASE_VERSION", "RELEASE_VERSION")
    if semver_core(source_version, "SOURCE_VERSION") != semver_core(
        release_id, "RELEASE_VERSION"
    ):
        raise SystemExit(
            "SOURCE_VERSION and RELEASE_VERSION must identify the same core version"
        )
    return ReleaseIdentity(source_version=source_version, release_id=release_id)


def extract_git_archive(root: pathlib.Path, commit: str, destination: pathlib.Path) -> None:
    archive_path = destination.parent / "repository.tar"
    with archive_path.open("wb") as handle:
        subprocess.run(
            ["git", "-C", str(root), "archive", "--format=tar", commit],
            check=True,
            stdout=handle,
        )
    with tarfile.open(archive_path, "r:") as archive:
        for member in archive.getmembers():
            path = pathlib.PurePosixPath(member.name)
            if not path.parts or path.is_absolute() or ".." in path.parts:
                raise SystemExit(f"unsafe Git archive path: {member.name}")
            if member.issym() or member.islnk() or member.isdev() or member.isfifo():
                raise SystemExit(f"unsupported Git archive member: {member.name}")
            if not (member.isdir() or member.isfile()):
                raise SystemExit(f"unsupported Git archive member: {member.name}")
            target = destination.joinpath(*path.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                os.chmod(target, 0o755)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise SystemExit(f"could not read Git archive member: {member.name}")
            with source, target.open("xb") as output:
                shutil.copyfileobj(source, output)
            os.chmod(target, 0o755 if member.mode & 0o111 else 0o644)


def add_tree_to_tar(
    archive: tarfile.TarFile,
    source: pathlib.Path,
    archive_root: str,
    timestamp: int,
) -> None:
    entries = [source] + sorted(
        source.rglob("*"),
        key=lambda path: path.relative_to(source).as_posix(),
    )
    for path in entries:
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise SystemExit(f"release tree contains a symbolic link: {path}")
        relative = path.relative_to(source)
        name = archive_root if not relative.parts else f"{archive_root}/{relative.as_posix()}"
        info = tarfile.TarInfo(name=name)
        info.uid = 0
        info.gid = 0
        info.uname = "root"
        info.gname = "root"
        info.mtime = timestamp
        if stat.S_ISDIR(metadata.st_mode):
            info.type = tarfile.DIRTYPE
            info.mode = 0o755
            archive.addfile(info)
        elif stat.S_ISREG(metadata.st_mode):
            if metadata.st_nlink != 1:
                raise SystemExit(f"release tree contains a hard-linked file: {path}")
            info.type = tarfile.REGTYPE
            info.mode = 0o755 if metadata.st_mode & stat.S_IXUSR else 0o644
            info.size = metadata.st_size
            with path.open("rb") as handle:
                archive.addfile(info, handle)
        else:
            raise SystemExit(f"release tree contains an unsupported file: {path}")


def build_archive(root: pathlib.Path, commit: str, version: str, output: pathlib.Path) -> int:
    archive_root = f"hostpanel-v{version}"
    timestamp = int(run_git(root, "show", "-s", "--format=%ct", commit))
    with tempfile.TemporaryDirectory(prefix="hostpanel-release.") as temporary_name:
        temporary = pathlib.Path(temporary_name)
        staging = temporary / archive_root
        staging.mkdir(mode=0o755)
        extract_git_archive(root, commit, staging)
        (staging / "VERSION").write_text(version + "\n", encoding="utf-8")
        os.chmod(staging / "VERSION", 0o644)
        tar_path = temporary / "release.tar"
        with tarfile.open(tar_path, "w", format=tarfile.PAX_FORMAT) as archive:
            add_tree_to_tar(archive, staging, archive_root, timestamp)
        with tar_path.open("rb") as source, output.open("xb") as raw:
            with gzip.GzipFile(
                filename="",
                mode="wb",
                fileobj=raw,
                mtime=0,
                compresslevel=9,
            ) as compressed:
                shutil.copyfileobj(source, compressed)
            raw.flush()
            os.fsync(raw.fileno())
    os.chmod(output, 0o644)
    return timestamp


def validate_archive(path: pathlib.Path, version: str) -> None:
    expected_root = f"hostpanel-v{version}"
    roots: set[str] = set()
    required = {
        f"{expected_root}/VERSION",
        f"{expected_root}/install.sh",
        f"{expected_root}/install.base.sh",
        f"{expected_root}/bootstrap-install.sh",
        f"{expected_root}/tools/harden_install.py",
        f"{expected_root}/tools/hostpanel-update.py",
        f"{expected_root}/tools/install-update-agent.sh",
    }
    discovered: set[str] = set()
    version_data: bytes | None = None
    with tarfile.open(path, "r:gz") as archive:
        members = archive.getmembers()
        if not members:
            raise SystemExit("release archive is empty")
        for member in members:
            pure = pathlib.PurePosixPath(member.name.rstrip("/"))
            if pure.is_absolute() or ".." in pure.parts or not pure.parts:
                raise SystemExit(f"unsafe archive path: {member.name}")
            roots.add(pure.parts[0])
            if member.issym() or member.islnk() or member.isdev() or member.isfifo():
                raise SystemExit(f"unsupported archive member: {member.name}")
            if not (member.isdir() or member.isfile()):
                raise SystemExit(f"unsupported archive member: {member.name}")
            expected_mode = 0o755 if member.isdir() or member.mode & 0o111 else 0o644
            if stat.S_IMODE(member.mode) != expected_mode:
                raise SystemExit(f"release archive mode is non-canonical: {member.name}")
            normalized = pure.as_posix()
            if normalized in discovered:
                raise SystemExit(f"release archive contains a duplicate path: {normalized}")
            discovered.add(normalized)
            if normalized == f"{expected_root}/VERSION":
                handle = archive.extractfile(member)
                if handle is None:
                    raise SystemExit("could not read release archive VERSION")
                version_data = handle.read(1024)
    if roots != {expected_root}:
        raise SystemExit("release archive has an unexpected root")
    missing = required - discovered
    if missing:
        raise SystemExit(f"release archive is missing required files: {sorted(missing)}")
    if version_data != (version + "\n").encode("utf-8"):
        raise SystemExit("release archive VERSION does not match SOURCE_VERSION")


def canonical_manifest(
    *,
    version: str,
    commit: str,
    channel: str,
    archive_name: str,
    archive_sha256: str,
) -> bytes:
    payload = {
        "schema": 1,
        "product": "hostpanel",
        "channel": channel,
        "version": version,
        "commit": commit,
        "tag": f"v{version}",
        "archive": {
            "name": archive_name,
            "sha256": archive_sha256,
            "signature": f"{archive_name}.sig",
        },
    }
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


def canonical_release_metadata(
    *,
    identity: ReleaseIdentity,
    commit: str,
    archive_name: str,
    archive_sha256: str,
    timestamp: int,
) -> bytes:
    built_at = dt.datetime.fromtimestamp(timestamp, tz=dt.timezone.utc).replace(
        microsecond=0
    )
    payload = {
        "schema": 1,
        "product": "hostpanel",
        "version": identity.source_version,
        "release_id": identity.release_id,
        "tag": f"v{identity.source_version}",
        "commit": commit,
        "archive": archive_name,
        "archive_sha256": archive_sha256,
        "built_at": built_at.isoformat(),
    }
    return (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode("utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", default=".")
    parser.add_argument("--commit")
    parser.add_argument("--channel", choices=("stable", "beta"), default="stable")
    parser.add_argument("--output-dir")
    parser.add_argument("--print-version", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = pathlib.Path(args.repository_root).resolve()
    identity = release_identity(root)
    version = identity.source_version
    if args.print_version:
        print(version)
        return 0
    if not args.output_dir:
        raise SystemExit("--output-dir is required unless --print-version is used")
    output_dir = pathlib.Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    commit = (args.commit or run_git(root, "rev-parse", "HEAD")).lower()
    if COMMIT_RE.fullmatch(commit) is None:
        raise SystemExit("commit must be a full 40-character SHA")
    resolved = run_git(root, "rev-parse", f"{commit}^{{commit}}").lower()
    if resolved != commit:
        raise SystemExit("commit does not resolve to the requested object")
    archive_name = f"hostpanel-v{version}-update.tar.gz"
    archive_path = output_dir / archive_name
    if archive_path.exists() or archive_path.is_symlink():
        raise SystemExit(f"refusing to overwrite existing release asset: {archive_path}")
    timestamp = build_archive(root, commit, version, archive_path)
    validate_archive(archive_path, version)
    digest = sha256_file(archive_path)
    manifest_path = output_dir / "hostpanel-update-manifest.json"
    if manifest_path.exists() or manifest_path.is_symlink():
        raise SystemExit(f"refusing to overwrite existing release asset: {manifest_path}")
    manifest_path.write_bytes(
        canonical_manifest(
            version=version,
            commit=commit,
            channel=args.channel,
            archive_name=archive_name,
            archive_sha256=digest,
        )
    )
    os.chmod(manifest_path, 0o644)
    metadata_path = output_dir / "release-build.json"
    if metadata_path.exists() or metadata_path.is_symlink():
        raise SystemExit(f"refusing to overwrite existing release asset: {metadata_path}")
    metadata_path.write_bytes(
        canonical_release_metadata(
            identity=identity,
            commit=commit,
            archive_name=archive_name,
            archive_sha256=digest,
            timestamp=timestamp,
        )
    )
    os.chmod(metadata_path, 0o644)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
