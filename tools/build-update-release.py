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

SEMVER_RE = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


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


def signed_source_version(root: pathlib.Path) -> str:
    archives = sorted(root.glob("hostpanel-v*-source.tar.gz"))
    if len(archives) != 1:
        raise SystemExit(f"expected exactly one signed source archive, found {len(archives)}")
    with tarfile.open(archives[0], "r:gz") as archive:
        matches = [
            member
            for member in archive.getmembers()
            if member.isfile() and member.name.endswith("/VERSION")
        ]
        if len(matches) != 1:
            raise SystemExit(
                f"expected exactly one VERSION in signed source archive, found {len(matches)}"
            )
        handle = archive.extractfile(matches[0])
        if handle is None:
            raise SystemExit("could not read signed source VERSION")
        version = handle.read().decode("utf-8", errors="strict").strip()
    if SEMVER_RE.fullmatch(version) is None:
        raise SystemExit(f"signed source VERSION is not semantic: {version!r}")
    return version


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
        if path.is_symlink():
            raise SystemExit(f"release tree contains a symbolic link: {path}")
        relative = path.relative_to(source)
        name = archive_root if not relative.parts else f"{archive_root}/{relative.as_posix()}"
        metadata = path.stat()
        info = tarfile.TarInfo(name=name)
        info.uid = 0
        info.gid = 0
        info.uname = "root"
        info.gname = "root"
        info.mtime = timestamp
        if path.is_dir():
            info.type = tarfile.DIRTYPE
            info.mode = 0o755
            archive.addfile(info)
        elif path.is_file():
            info.type = tarfile.REGTYPE
            info.mode = 0o755 if metadata.st_mode & stat.S_IXUSR else 0o644
            info.size = metadata.st_size
            with path.open("rb") as handle:
                archive.addfile(info, handle)
        else:
            raise SystemExit(f"release tree contains an unsupported file: {path}")


def build_archive(root: pathlib.Path, commit: str, version: str, output: pathlib.Path) -> None:
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
    with tarfile.open(path, "r:gz") as archive:
        members = archive.getmembers()
        if not members:
            raise SystemExit("release archive is empty")
        for member in members:
            pure = pathlib.PurePosixPath(member.name)
            if pure.is_absolute() or ".." in pure.parts or not pure.parts:
                raise SystemExit(f"unsafe archive path: {member.name}")
            roots.add(pure.parts[0])
            if member.issym() or member.islnk() or member.isdev() or member.isfifo():
                raise SystemExit(f"unsupported archive member: {member.name}")
            discovered.add(member.name.rstrip("/"))
    if roots != {expected_root}:
        raise SystemExit("release archive has an unexpected root")
    missing = required - discovered
    if missing:
        raise SystemExit(f"release archive is missing required files: {sorted(missing)}")


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
    version = signed_source_version(root)
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
    if archive_path.exists():
        archive_path.unlink()
    build_archive(root, commit, version, archive_path)
    validate_archive(archive_path, version)
    digest = sha256_file(archive_path)
    manifest_path = output_dir / "hostpanel-update-manifest.json"
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
    metadata = {
        "version": version,
        "tag": f"v{version}",
        "commit": commit,
        "archive": archive_name,
        "archive_sha256": digest,
        "built_at": dt.datetime.now(dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat(),
    }
    (output_dir / "release-build.json").write_text(
        json.dumps(metadata, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
