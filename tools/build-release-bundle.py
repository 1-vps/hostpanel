#!/usr/bin/env python3
"""Build and verify the complete unsigned HostPanel release payload set."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import pathlib
import shutil
import stat
import subprocess
import sys
import tempfile
from types import ModuleType
from typing import Sequence


SOURCE_METADATA = (
    "source-build.json",
    "sbom.cdx.json",
    "dependency-lock-attestation.json",
    "source-file-manifest.json",
)
SOURCE_CHECKSUMS = "SOURCE-CANDIDATE-SHA256SUMS"
UPDATE_METADATA = (
    "hostpanel-update-manifest.json",
    "release-build.json",
)
MAX_FILE_BYTES = 2 * 1024 * 1024 * 1024


class ReleaseBundleError(RuntimeError):
    """Raised when the unsigned release bundle cannot be reproduced safely."""


def load_module(name: str, path: pathlib.Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ReleaseBundleError(f"could not load release module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def run(arguments: Sequence[str], *, cwd: pathlib.Path | None = None) -> None:
    try:
        subprocess.run(
            list(arguments),
            cwd=cwd,
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
    except FileNotFoundError as exc:
        raise ReleaseBundleError(f"required command is unavailable: {arguments[0]}") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        raise ReleaseBundleError(
            f"command failed: {' '.join(arguments)}{': ' + detail if detail else ''}"
        ) from exc


def sha256_file(path: pathlib.Path) -> str:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ReleaseBundleError(f"release payload is missing: {path}") from exc
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise ReleaseBundleError(f"release payload is not a single-linked regular file: {path}")
    if metadata.st_size <= 0 or metadata.st_size > MAX_FILE_BYTES:
        raise ReleaseBundleError(f"release payload has an unsafe size: {path}")
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            total += len(chunk)
            if total > MAX_FILE_BYTES:
                raise ReleaseBundleError(f"release payload exceeded the size limit: {path}")
            digest.update(chunk)
    if total != metadata.st_size:
        raise ReleaseBundleError(f"release payload changed size while hashing: {path}")
    return digest.hexdigest()


def copy_exclusive(source: pathlib.Path, destination: pathlib.Path) -> None:
    if destination.exists() or destination.is_symlink():
        raise ReleaseBundleError(f"refusing to overwrite release payload: {destination}")
    try:
        metadata = source.lstat()
    except OSError as exc:
        raise ReleaseBundleError(f"release payload is missing: {source}") from exc
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise ReleaseBundleError(f"release payload is unsafe: {source}")
    with source.open("rb") as input_handle, destination.open("xb") as output_handle:
        shutil.copyfileobj(input_handle, output_handle, length=1024 * 1024)
        output_handle.flush()
        os.fsync(output_handle.fileno())
    os.chmod(destination, 0o644)
    if sha256_file(source) != sha256_file(destination):
        raise ReleaseBundleError(f"copied release payload digest mismatch: {source.name}")


def write_source_checksums(directory: pathlib.Path, archive_name: str) -> None:
    names = (archive_name, *SOURCE_METADATA)
    checksum_path = directory / SOURCE_CHECKSUMS
    if checksum_path.exists() or checksum_path.is_symlink():
        raise ReleaseBundleError(f"refusing to overwrite source checksums: {checksum_path}")
    content = "".join(f"{sha256_file(directory / name)}  {name}\n" for name in names)
    with checksum_path.open("xb") as handle:
        handle.write(content.encode("utf-8"))
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(checksum_path, 0o644)


def exact_regular_files(directory: pathlib.Path) -> set[str]:
    result: set[str] = set()
    for path in directory.iterdir():
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise ReleaseBundleError(f"release output contains an unsafe entry: {path.name}")
        result.add(path.name)
    return result


def require_empty_output_directory(output_dir: pathlib.Path) -> None:
    if output_dir.exists() or output_dir.is_symlink():
        if output_dir.is_symlink() or not output_dir.is_dir():
            raise ReleaseBundleError("release output path is not a safe directory")
        if any(output_dir.iterdir()):
            raise ReleaseBundleError("release output directory must be empty")
    else:
        output_dir.mkdir(parents=True, mode=0o755)


def build(
    repository_root: pathlib.Path,
    commit: str,
    channel: str,
    output_dir: pathlib.Path,
) -> dict[str, object]:
    require_empty_output_directory(output_dir)

    update_builder = load_module(
        "hostpanel_release_bundle_update_builder",
        repository_root / "tools" / "build-update-release.py",
    )
    source_verifier = load_module(
        "hostpanel_release_bundle_source_verifier",
        repository_root / "tools" / "verify-source-release-bundle.py",
    )
    existing_verifier = load_module(
        "hostpanel_release_bundle_update_verifier",
        repository_root / "tools" / "verify-existing-release.py",
    )
    identity = update_builder.release_identity(repository_root)
    try:
        resolved = update_builder.run_git(
            repository_root, "rev-parse", f"{commit.lower()}^{{commit}}"
        ).lower()
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReleaseBundleError("release commit could not be resolved") from exc
    if resolved != commit.lower() or update_builder.COMMIT_RE.fullmatch(resolved) is None:
        raise ReleaseBundleError("release commit does not resolve to the exact requested object")
    commit = resolved

    with tempfile.TemporaryDirectory(prefix="hostpanel-release-bundle.") as temporary_name:
        temporary = pathlib.Path(temporary_name)
        source_dir = temporary / "source"
        update_dir = temporary / "update"
        source_dir.mkdir(mode=0o700)
        update_dir.mkdir(mode=0o700)

        run(
            [
                "python3",
                str(repository_root / "tools" / "run-source-release-builder.py"),
                "--repository-root",
                str(repository_root),
                "--commit",
                commit,
                "--output-dir",
                str(source_dir),
            ]
        )
        source_archive_name = f"hostpanel-v{identity.release_id}-source.tar.gz"
        write_source_checksums(source_dir, source_archive_name)
        try:
            source_verifier.verify(
                source_dir,
                repository_root,
                commit,
                identity.source_version,
                identity.release_id,
            )
        except Exception as exc:
            raise ReleaseBundleError(f"source release bundle verification failed: {exc}") from exc

        run(
            [
                "python3",
                str(repository_root / "tools" / "build-update-release.py"),
                "--repository-root",
                str(repository_root),
                "--commit",
                commit,
                "--channel",
                channel,
                "--output-dir",
                str(update_dir),
            ]
        )
        update_archive_name = f"hostpanel-v{identity.source_version}-update.tar.gz"
        try:
            existing_verifier.verify(
                update_dir / "hostpanel-update-manifest.json",
                update_dir / update_archive_name,
                update_dir / "release-build.json",
                commit,
                identity.source_version,
                identity.release_id,
            )
        except SystemExit as exc:
            raise ReleaseBundleError(f"update release bundle verification failed: {exc}") from exc

        source_payloads = (
            source_archive_name,
            *SOURCE_METADATA,
            SOURCE_CHECKSUMS,
        )
        update_payloads = (
            update_archive_name,
            *UPDATE_METADATA,
        )
        for name in source_payloads:
            copy_exclusive(source_dir / name, output_dir / name)
        for name in update_payloads:
            copy_exclusive(update_dir / name, output_dir / name)

    expected_names = set(source_payloads) | set(update_payloads)
    actual_names = exact_regular_files(output_dir)
    if actual_names != expected_names:
        raise ReleaseBundleError(
            f"release output does not contain exactly the reviewed payload set: {sorted(actual_names)}"
        )
    payloads = {name: sha256_file(output_dir / name) for name in sorted(expected_names)}
    return {
        "schema": 1,
        "product": "hostpanel",
        "version": identity.source_version,
        "release_id": identity.release_id,
        "tag": f"v{identity.source_version}",
        "commit": commit,
        "channel": channel,
        "payloads": payloads,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=pathlib.Path, default=pathlib.Path("."))
    parser.add_argument("--commit", required=True)
    parser.add_argument("--channel", choices=("stable", "beta"), default="stable")
    parser.add_argument("--output-dir", type=pathlib.Path, required=True)
    parser.add_argument("--summary", type=pathlib.Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        summary = build(
            args.repository_root.resolve(),
            args.commit,
            args.channel,
            args.output_dir.resolve(),
        )
        encoded = (
            json.dumps(summary, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        if args.summary is not None:
            summary_path = args.summary.resolve()
            if summary_path.exists() or summary_path.is_symlink():
                raise ReleaseBundleError(f"refusing to overwrite bundle summary: {summary_path}")
            summary_path.parent.mkdir(parents=True, exist_ok=True)
            with summary_path.open("xb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(summary_path, 0o644)
        else:
            sys.stdout.buffer.write(encoded)
    except ReleaseBundleError as exc:
        print(f"Release bundle build failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
