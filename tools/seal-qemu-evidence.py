#!/usr/bin/env python3
"""Create an immutable, sanitized snapshot for QEMU evidence upload."""

from __future__ import annotations

import importlib.util
import io
import os
import pathlib
import stat
import sys
import tarfile
import tempfile
from types import ModuleType


SANITIZER_PATH = pathlib.Path(__file__).with_name("sanitize-qemu-evidence.py")
ARCHIVE_ROOT = pathlib.PurePosixPath("qemu-vm-acceptance")


def _load_sanitizer() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "qemu_evidence_sanitizer_for_sealing",
        SANITIZER_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load QEMU evidence sanitizer")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SANITIZER = _load_sanitizer()


def _require_safe_root(root: pathlib.Path) -> pathlib.Path:
    resolved = root.resolve(strict=True)
    metadata = resolved.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise RuntimeError("unsafe evidence root")
    return resolved


def _require_safe_output(root: pathlib.Path, archive: pathlib.Path) -> pathlib.Path:
    SANITIZER._require_safe_name(archive)
    parent = archive.parent.resolve(strict=True)
    parent_metadata = parent.lstat()
    if stat.S_ISLNK(parent_metadata.st_mode) or not stat.S_ISDIR(parent_metadata.st_mode):
        raise RuntimeError("unsafe sealed evidence parent")
    resolved = parent / archive.name
    if resolved.exists() or resolved.is_symlink():
        raise RuntimeError("sealed evidence archive already exists")
    try:
        resolved.relative_to(root)
    except ValueError:
        pass
    else:
        raise RuntimeError("sealed evidence archive must be outside the evidence tree")
    return resolved


def _archive_name(root: pathlib.Path, path: pathlib.Path) -> str:
    relative = path.relative_to(root)
    return (ARCHIVE_ROOT / pathlib.PurePosixPath(*relative.parts)).as_posix()


def _fsync_directory(directory: pathlib.Path) -> None:
    descriptor = os.open(
        directory,
        os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_DIRECTORY", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def seal_tree(root: pathlib.Path, archive: pathlib.Path) -> tuple[int, int]:
    root = _require_safe_root(root)
    archive = _require_safe_output(root, archive)
    paths = sorted(
        SANITIZER._regular_files(root),
        key=lambda path: os.fsencode(str(path.relative_to(root))),
    )
    if not paths:
        raise RuntimeError("evidence tree contains no regular files")

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{archive.name}.seal.",
        dir=archive.parent,
    )
    temporary_path = pathlib.Path(temporary_name)
    published = False
    total_bytes = 0
    try:
        with os.fdopen(descriptor, "wb") as output:
            with tarfile.open(
                fileobj=output,
                mode="w",
                format=tarfile.PAX_FORMAT,
            ) as sealed:
                for path in paths:
                    data = SANITIZER._sanitize(SANITIZER._read_stable(path))
                    total_bytes += len(data)
                    if total_bytes > SANITIZER.MAX_TOTAL_BYTES:
                        raise RuntimeError("sealed evidence exceeds total size limit")
                    member = tarfile.TarInfo(_archive_name(root, path))
                    member.size = len(data)
                    member.mode = 0o600
                    member.uid = 0
                    member.gid = 0
                    member.uname = ""
                    member.gname = ""
                    member.mtime = 0
                    member.type = tarfile.REGTYPE
                    sealed.addfile(member, io.BytesIO(data))
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary_path, 0o600)
        os.link(temporary_path, archive, follow_symlinks=False)
        published = True
        temporary_path.unlink()
        _fsync_directory(archive.parent)

        metadata = archive.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise RuntimeError("sealed evidence archive metadata is unsafe")
        return len(paths), total_bytes
    except Exception:
        if published:
            archive.unlink(missing_ok=True)
        raise
    finally:
        temporary_path.unlink(missing_ok=True)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(
            f"usage: {argv[0]} EVIDENCE_DIRECTORY OUTPUT_ARCHIVE",
            file=sys.stderr,
        )
        return 2
    try:
        file_count, total_bytes = seal_tree(
            pathlib.Path(argv[1]),
            pathlib.Path(argv[2]),
        )
    except OSError as error:
        error_number = error.errno if error.errno is not None else "unknown"
        print(
            f"QEMU evidence sealing failed: filesystem error ({error_number})",
            file=sys.stderr,
        )
        return 1
    except (RuntimeError, tarfile.TarError) as error:
        print(f"QEMU evidence sealing failed: {error}", file=sys.stderr)
        return 1
    print(
        f"QEMU evidence sealing passed: {file_count} files, "
        f"{total_bytes} sanitized bytes."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
