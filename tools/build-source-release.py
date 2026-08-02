#!/usr/bin/env python3
"""Build and verify a deterministic HostPanel source-release candidate.

The builder starts from the currently signed source baseline, verifies that
baseline with the anchored update key, overlays only commit-addressed paths
listed in a reviewed policy, applies the same generated UI/localization steps
as bootstrap, and emits an unsigned deterministic candidate. Signing remains a
separate protected-environment operation.
"""

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
from typing import Iterable, Sequence

SEMVER_RE = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SOURCE_ARCHIVE_RE = re.compile(
    r"^hostpanel-v(?P<release_id>[0-9]+(?:\.[0-9]+){2}"
    r"(?:[-+][0-9A-Za-z][0-9A-Za-z.-]*)?)-source\.tar\.gz$"
)
MAX_MEMBERS = 50_000
MAX_EXPANDED_BYTES = 2 * 1024 * 1024 * 1024
MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024
MAX_SIGNATURE_BYTES = 64 * 1024
POLICY_KEYS = {"schema", "overlay_paths", "required_paths"}


class ReleaseBuildError(RuntimeError):
    """Raised when source-release inputs or outputs fail closed."""


@dataclass(frozen=True)
class ReleaseIdentity:
    source_version: str
    release_id: str

    @property
    def archive_name(self) -> str:
        return f"hostpanel-v{self.release_id}-source.tar.gz"

    @property
    def archive_root(self) -> str:
        return f"hostpanel-v{self.release_id}"


@dataclass(frozen=True)
class SourcePolicy:
    overlay_paths: tuple[str, ...]
    required_paths: tuple[str, ...]


@dataclass(frozen=True)
class Baseline:
    archive_path: pathlib.Path
    archive_name: str
    release_id: str
    sha256: str


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(
    arguments: Sequence[str],
    *,
    cwd: pathlib.Path | None = None,
    stdout=None,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(arguments),
            cwd=cwd,
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=stdout if stdout is not None else subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError as exc:
        raise ReleaseBuildError(f"required command is unavailable: {arguments[0]}") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip()
        raise ReleaseBuildError(
            f"command failed: {' '.join(arguments)}{': ' + detail if detail else ''}"
        ) from exc


def run_git(root: pathlib.Path, *arguments: str) -> str:
    return run(["git", "-C", str(root), *arguments]).stdout.strip()


def read_single_line(path: pathlib.Path, label: str) -> str:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ReleaseBuildError(f"{label} is missing: {path}") from exc
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise ReleaseBuildError(f"{label} must be a single-linked regular file")
    raw = path.read_text(encoding="utf-8", errors="strict")
    lines = raw.splitlines()
    if (
        len(lines) != 1
        or raw not in {lines[0], lines[0] + "\n"}
        or not lines[0]
        or lines[0].strip() != lines[0]
    ):
        raise ReleaseBuildError(f"{label} must contain exactly one clean non-empty line")
    return lines[0]


def semver_core(value: str) -> str:
    match = SEMVER_RE.fullmatch(value)
    if match is None:
        raise ReleaseBuildError(f"invalid semantic version: {value!r}")
    return ".".join(match.group(index) for index in (1, 2, 3))


def load_identity(root: pathlib.Path) -> ReleaseIdentity:
    source_version = read_single_line(root / "SOURCE_VERSION", "SOURCE_VERSION")
    release_id = read_single_line(root / "RELEASE_VERSION", "RELEASE_VERSION")
    if semver_core(source_version) != semver_core(release_id):
        raise ReleaseBuildError(
            "SOURCE_VERSION and RELEASE_VERSION must identify the same core version"
        )
    return ReleaseIdentity(source_version=source_version, release_id=release_id)


def validate_relative_path(raw: object, label: str) -> str:
    if not isinstance(raw, str) or not raw:
        raise ReleaseBuildError(f"{label} must be a non-empty string")
    pure = pathlib.PurePosixPath(raw)
    if pure.is_absolute() or raw != pure.as_posix():
        raise ReleaseBuildError(f"{label} must be a normalized relative POSIX path: {raw!r}")
    if any(part in ("", ".", "..") for part in pure.parts):
        raise ReleaseBuildError(f"{label} contains an unsafe component: {raw!r}")
    if pure.parts[0] == ".git":
        raise ReleaseBuildError(f"{label} may not expose Git metadata")
    return raw


def load_policy(path: pathlib.Path) -> SourcePolicy:
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="strict"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseBuildError(f"could not read source-release policy: {path}") from exc
    if not isinstance(payload, dict) or set(payload) != POLICY_KEYS:
        raise ReleaseBuildError("source-release policy has an unexpected shape")
    if payload["schema"] != 1:
        raise ReleaseBuildError("unsupported source-release policy schema")

    def parse_list(key: str) -> tuple[str, ...]:
        values = payload[key]
        if not isinstance(values, list) or not values:
            raise ReleaseBuildError(f"policy field {key} must be a non-empty list")
        parsed = tuple(validate_relative_path(item, f"{key} entry") for item in values)
        if len(parsed) != len(set(parsed)):
            raise ReleaseBuildError(f"policy field {key} contains duplicates")
        if parsed != tuple(sorted(parsed)):
            raise ReleaseBuildError(f"policy field {key} must be sorted")
        return parsed

    return SourcePolicy(
        overlay_paths=parse_list("overlay_paths"),
        required_paths=parse_list("required_paths"),
    )


def ensure_regular_file(path: pathlib.Path, label: str, maximum_size: int) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ReleaseBuildError(f"{label} is missing: {path}") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise ReleaseBuildError(f"{label} is not a regular file")
    if metadata.st_nlink != 1:
        raise ReleaseBuildError(f"{label} must have exactly one hard link")
    if metadata.st_size <= 0 or metadata.st_size > maximum_size:
        raise ReleaseBuildError(f"{label} has an unsafe size")
    return metadata


def locate_baseline(root: pathlib.Path) -> Baseline:
    checksums_path = root / "SHA256SUMS"
    ensure_regular_file(checksums_path, "SHA256SUMS", 1024 * 1024)
    matches: list[tuple[str, str, str]] = []
    for raw_line in checksums_path.read_text(encoding="utf-8", errors="strict").splitlines():
        fields = raw_line.split()
        if len(fields) != 2:
            continue
        digest, name = fields
        archive_match = SOURCE_ARCHIVE_RE.fullmatch(name)
        if archive_match and SHA256_RE.fullmatch(digest):
            matches.append((digest, name, archive_match.group("release_id")))
    if len(matches) != 1:
        raise ReleaseBuildError(
            f"SHA256SUMS must identify exactly one source tarball, found {len(matches)}"
        )
    expected_digest, archive_name, release_id = matches[0]
    archive_path = root / archive_name
    ensure_regular_file(archive_path, "baseline source archive", MAX_ARCHIVE_BYTES)
    actual_digest = sha256_file(archive_path)
    if actual_digest != expected_digest:
        raise ReleaseBuildError("baseline source archive checksum mismatch")
    signature_path = pathlib.Path(f"{archive_path}.sig")
    ensure_regular_file(signature_path, "baseline source signature", MAX_SIGNATURE_BYTES)
    public_key = root / "releases" / "update.pub"
    ensure_regular_file(public_key, "release public key", MAX_SIGNATURE_BYTES)
    run(
        [
            "openssl",
            "pkeyutl",
            "-verify",
            "-pubin",
            "-inkey",
            str(public_key),
            "-rawin",
            "-in",
            str(archive_path),
            "-sigfile",
            str(signature_path),
        ]
    )
    return Baseline(
        archive_path=archive_path,
        archive_name=archive_name,
        release_id=release_id,
        sha256=actual_digest,
    )


def member_path(name: str) -> pathlib.PurePosixPath:
    pure = pathlib.PurePosixPath(name)
    if not pure.parts or pure.is_absolute() or ".." in pure.parts:
        raise ReleaseBuildError(f"unsafe archive path: {name}")
    if any(part in ("", ".") for part in pure.parts):
        raise ReleaseBuildError(f"non-normalized archive path: {name}")
    return pure


def extract_tar_safely(
    archive_path: pathlib.Path,
    destination: pathlib.Path,
    *,
    require_single_root: bool,
) -> tuple[str, ...]:
    destination.mkdir(parents=True, exist_ok=False)
    seen: set[str] = set()
    roots: set[str] = set()
    total_size = 0
    with tarfile.open(archive_path, "r:*") as archive:
        members = archive.getmembers()
        if not members or len(members) > MAX_MEMBERS:
            raise ReleaseBuildError("archive is empty or contains too many members")
        for member in members:
            pure = member_path(member.name.rstrip("/"))
            normalized = pure.as_posix()
            if normalized in seen:
                raise ReleaseBuildError(f"duplicate archive path: {normalized}")
            seen.add(normalized)
            roots.add(pure.parts[0])
            if member.issym() or member.islnk() or member.isdev() or member.isfifo():
                raise ReleaseBuildError(f"unsupported archive member: {member.name}")
            if not (member.isdir() or member.isfile()):
                raise ReleaseBuildError(f"unsupported archive member: {member.name}")
            total_size += max(member.size, 0)
            if total_size > MAX_EXPANDED_BYTES:
                raise ReleaseBuildError("archive expands beyond the safety limit")
        if require_single_root and len(roots) != 1:
            raise ReleaseBuildError("archive must contain exactly one top-level root")

        for member in members:
            pure = member_path(member.name.rstrip("/"))
            target = destination.joinpath(*pure.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                os.chmod(target, 0o755)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                raise ReleaseBuildError(f"archive target already exists: {pure.as_posix()}")
            source = archive.extractfile(member)
            if source is None:
                raise ReleaseBuildError(f"could not read archive member: {member.name}")
            with source, target.open("xb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
            os.chmod(target, 0o755 if member.mode & 0o111 else 0o644)
    return tuple(sorted(roots))


def verify_commit(root: pathlib.Path, commit: str) -> str:
    normalized = commit.lower()
    if COMMIT_RE.fullmatch(normalized) is None:
        raise ReleaseBuildError("commit must be a full 40-character lowercase SHA")
    resolved = run_git(root, "rev-parse", f"{normalized}^{{commit}}").lower()
    if resolved != normalized:
        raise ReleaseBuildError("commit does not resolve to the requested object")
    return normalized


def export_overlay(
    root: pathlib.Path,
    commit: str,
    policy: SourcePolicy,
    destination: pathlib.Path,
) -> None:
    for path in policy.overlay_paths:
        run_git(root, "cat-file", "-e", f"{commit}:{path}")
    archive_path = destination.parent / "overlay.tar"
    with archive_path.open("xb") as output:
        run(
            [
                "git",
                "-C",
                str(root),
                "archive",
                "--format=tar",
                commit,
                "--",
                *policy.overlay_paths,
            ],
            stdout=output,
        )
    extract_tar_safely(archive_path, destination, require_single_root=False)


def copy_overlay(overlay_root: pathlib.Path, source_root: pathlib.Path) -> None:
    entries = sorted(
        overlay_root.rglob("*"),
        key=lambda path: path.relative_to(overlay_root).as_posix(),
    )
    for source in entries:
        relative = source.relative_to(overlay_root)
        target = source_root / relative
        metadata = source.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise ReleaseBuildError(f"overlay contains a symbolic link: {relative}")
        if stat.S_ISDIR(metadata.st_mode):
            if target.exists() and not target.is_dir():
                raise ReleaseBuildError(f"overlay type conflict at {relative}")
            target.mkdir(parents=True, exist_ok=True)
            os.chmod(target, 0o755)
        elif stat.S_ISREG(metadata.st_mode):
            if target.exists() and not target.is_file():
                raise ReleaseBuildError(f"overlay type conflict at {relative}")
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{target.name}.source-release.tmp")
            if temporary.exists():
                raise ReleaseBuildError(f"stale overlay temporary exists: {relative}")
            with source.open("rb") as input_handle, temporary.open("xb") as output_handle:
                shutil.copyfileobj(input_handle, output_handle, length=1024 * 1024)
                output_handle.flush()
                os.fsync(output_handle.fileno())
            os.chmod(temporary, 0o755 if metadata.st_mode & stat.S_IXUSR else 0o644)
            os.replace(temporary, target)
        else:
            raise ReleaseBuildError(f"overlay contains an unsupported entry: {relative}")


def run_post_processing(
    source_root: pathlib.Path,
    repository_root: pathlib.Path,
    identity: ReleaseIdentity,
) -> None:
    version_path = source_root / "VERSION"
    version_path.write_text(identity.source_version + "\n", encoding="utf-8")
    os.chmod(version_path, 0o644)

    panel_patcher = source_root / "tools" / "patch_panel_ui.py"
    panel_template = source_root / "app" / "templates" / "panel.html"
    run(["python3", str(panel_patcher), str(panel_template)])

    localization_root = repository_root / "localization-overlay"
    run(
        [
            "python3",
            str(localization_root / "apply_localization_overlay_reviewed.py"),
            str(source_root),
            str(localization_root),
        ]
    )
    run(["python3", str(source_root / "tools" / "audit_locales.py")])
    run(["python3", str(source_root / "tools" / "review_locales.py")])

    with tempfile.TemporaryDirectory(prefix="hostpanel-generated-installer.") as temp_name:
        generated = pathlib.Path(temp_name) / "install.generated.sh"
        run(
            [
                "python3",
                str(source_root / "tools" / "harden_install.py"),
                str(source_root / "install.base.sh"),
                str(generated),
            ]
        )
        run(["bash", "-n", str(source_root / "install.sh")])
        run(["bash", "-n", str(generated)])


def validate_required_paths(source_root: pathlib.Path, policy: SourcePolicy) -> None:
    for raw in policy.required_paths:
        path = source_root.joinpath(*pathlib.PurePosixPath(raw).parts)
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise ReleaseBuildError(f"required source path is missing: {raw}") from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise ReleaseBuildError(f"required source path is a symbolic link: {raw}")
        if not (stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)):
            raise ReleaseBuildError(f"required source path has an unsafe type: {raw}")


def iter_source_entries(source_root: pathlib.Path) -> Iterable[pathlib.Path]:
    yield source_root
    yield from sorted(
        source_root.rglob("*"),
        key=lambda path: path.relative_to(source_root).as_posix(),
    )


def write_deterministic_archive(
    source_root: pathlib.Path,
    output_path: pathlib.Path,
    identity: ReleaseIdentity,
    timestamp: int,
) -> None:
    if output_path.exists():
        raise ReleaseBuildError(f"refusing to overwrite existing output: {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="hostpanel-source-tar.") as temp_name:
        tar_path = pathlib.Path(temp_name) / "source.tar"
        with tarfile.open(tar_path, "x", format=tarfile.PAX_FORMAT) as archive:
            for path in iter_source_entries(source_root):
                metadata = path.lstat()
                if stat.S_ISLNK(metadata.st_mode):
                    raise ReleaseBuildError(f"source tree contains a symbolic link: {path}")
                relative = path.relative_to(source_root)
                archive_name = (
                    identity.archive_root
                    if not relative.parts
                    else f"{identity.archive_root}/{relative.as_posix()}"
                )
                info = tarfile.TarInfo(archive_name)
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
                    info.type = tarfile.REGTYPE
                    info.mode = 0o755 if metadata.st_mode & stat.S_IXUSR else 0o644
                    info.size = metadata.st_size
                    with path.open("rb") as handle:
                        archive.addfile(info, handle)
                else:
                    raise ReleaseBuildError(f"source tree contains an unsupported entry: {path}")
        with tar_path.open("rb") as source, output_path.open("xb") as raw:
            with gzip.GzipFile(
                filename="",
                mode="wb",
                fileobj=raw,
                mtime=0,
                compresslevel=9,
            ) as compressed:
                shutil.copyfileobj(source, compressed, length=1024 * 1024)
            raw.flush()
            os.fsync(raw.fileno())
    os.chmod(output_path, 0o644)


def validate_built_archive(
    archive_path: pathlib.Path,
    identity: ReleaseIdentity,
    policy: SourcePolicy,
    timestamp: int,
) -> None:
    ensure_regular_file(archive_path, "built source archive", MAX_ARCHIVE_BYTES)
    expected_root = identity.archive_root
    expected_required = {
        f"{expected_root}/{path}" for path in policy.required_paths
    } | {f"{expected_root}/VERSION"}
    discovered: set[str] = set()
    version_data: bytes | None = None
    with tarfile.open(archive_path, "r:gz") as archive:
        members = archive.getmembers()
        if not members or len(members) > MAX_MEMBERS:
            raise ReleaseBuildError("built archive is empty or contains too many members")
        for member in members:
            pure = member_path(member.name.rstrip("/"))
            if pure.parts[0] != expected_root:
                raise ReleaseBuildError("built archive has an unexpected top-level root")
            if member.issym() or member.islnk() or member.isdev() or member.isfifo():
                raise ReleaseBuildError(f"built archive contains an unsafe member: {member.name}")
            if not (member.isdir() or member.isfile()):
                raise ReleaseBuildError(f"built archive contains an unsupported member: {member.name}")
            if member.uid != 0 or member.gid != 0 or member.uname != "root" or member.gname != "root":
                raise ReleaseBuildError(f"built archive ownership is non-canonical: {member.name}")
            if int(member.mtime) != timestamp:
                raise ReleaseBuildError(f"built archive timestamp is non-canonical: {member.name}")
            expected_mode = 0o755 if member.isdir() or member.mode & 0o111 else 0o644
            if stat.S_IMODE(member.mode) != expected_mode:
                raise ReleaseBuildError(f"built archive mode is non-canonical: {member.name}")
            normalized = pure.as_posix()
            if normalized in discovered:
                raise ReleaseBuildError(f"built archive contains a duplicate path: {normalized}")
            discovered.add(normalized)
            if normalized == f"{expected_root}/VERSION":
                handle = archive.extractfile(member)
                if handle is None:
                    raise ReleaseBuildError("could not read built VERSION")
                version_data = handle.read(1024)
    missing = expected_required - discovered
    if missing:
        raise ReleaseBuildError(f"built archive is missing required paths: {sorted(missing)}")
    if version_data != (identity.source_version + "\n").encode("utf-8"):
        raise ReleaseBuildError("built archive VERSION does not match SOURCE_VERSION")


def build(root: pathlib.Path, commit: str, output_dir: pathlib.Path, policy_path: pathlib.Path) -> pathlib.Path:
    identity = load_identity(root)
    policy = load_policy(policy_path)
    commit = verify_commit(root, commit)
    baseline = locate_baseline(root)
    timestamp = int(run_git(root, "show", "-s", "--format=%ct", commit))
    built_at = dt.datetime.fromtimestamp(timestamp, tz=dt.timezone.utc).isoformat()

    with tempfile.TemporaryDirectory(prefix="hostpanel-source-release.") as temp_name:
        temporary = pathlib.Path(temp_name)
        baseline_extract = temporary / "baseline"
        roots = extract_tar_safely(
            baseline.archive_path,
            baseline_extract,
            require_single_root=True,
        )
        source_root = baseline_extract / roots[0]
        if not source_root.is_dir():
            raise ReleaseBuildError("baseline top-level root is not a directory")
        overlay_root = temporary / "overlay"
        export_overlay(root, commit, policy, overlay_root)
        copy_overlay(overlay_root, source_root)
        run_post_processing(source_root, root, identity)
        validate_required_paths(source_root, policy)

        output_dir.mkdir(parents=True, exist_ok=True)
        archive_path = output_dir / identity.archive_name
        write_deterministic_archive(source_root, archive_path, identity, timestamp)
        validate_built_archive(archive_path, identity, policy, timestamp)
        archive_digest = sha256_file(archive_path)
        metadata = {
            "schema": 1,
            "product": "hostpanel",
            "source_version": identity.source_version,
            "release_id": identity.release_id,
            "commit": commit,
            "built_at": built_at,
            "baseline": {
                "archive": baseline.archive_name,
                "release_id": baseline.release_id,
                "sha256": baseline.sha256,
            },
            "archive": {
                "name": archive_path.name,
                "sha256": archive_digest,
            },
            "overlay_paths": list(policy.overlay_paths),
        }
        metadata_path = output_dir / "source-build.json"
        if metadata_path.exists():
            raise ReleaseBuildError(f"refusing to overwrite existing output: {metadata_path}")
        metadata_path.write_text(
            json.dumps(metadata, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        os.chmod(metadata_path, 0o644)
        return archive_path


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", default=".")
    parser.add_argument("--commit")
    parser.add_argument("--output-dir")
    parser.add_argument("--policy", default="releases/source-release-policy.json")
    parser.add_argument("--print-version", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    root = pathlib.Path(args.repository_root).resolve()
    identity = load_identity(root)
    if args.print_version:
        print(identity.source_version)
        return 0
    if not args.output_dir:
        raise ReleaseBuildError("--output-dir is required unless --print-version is used")
    commit = (args.commit or run_git(root, "rev-parse", "HEAD")).lower()
    policy_path = pathlib.Path(args.policy)
    if not policy_path.is_absolute():
        policy_path = root / policy_path
    archive_path = build(root, commit, pathlib.Path(args.output_dir).resolve(), policy_path)
    print(archive_path)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ReleaseBuildError as error:
        print(f"Source release build failed: {error}", file=os.sys.stderr)
        raise SystemExit(1)
