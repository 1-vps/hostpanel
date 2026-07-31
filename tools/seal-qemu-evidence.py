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
from types import ModuleType
from typing import Any


SANITIZER_PATH = pathlib.Path(__file__).with_name("sanitize-qemu-evidence.py")
ARCHIVE_ROOT = pathlib.PurePosixPath("qemu-vm-acceptance")
_DIRECTORY_FLAGS = (
    os.O_RDONLY
    | os.O_CLOEXEC
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)


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


def _directory_signature(metadata: Any) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_gid,
    )


def _open_stable_directory(path: pathlib.Path, label: str) -> int:
    initial = path.lstat()
    if stat.S_ISLNK(initial.st_mode) or not stat.S_ISDIR(initial.st_mode):
        raise RuntimeError(f"unsafe {label}")
    descriptor = os.open(path, _DIRECTORY_FLAGS)
    opened = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(opened.st_mode)
        or _directory_signature(initial) != _directory_signature(opened)
    ):
        os.close(descriptor)
        raise RuntimeError(f"{label} changed while opening")
    return descriptor


def _descriptor_path(descriptor: int) -> pathlib.Path:
    return pathlib.Path(f"/proc/self/fd/{descriptor}")


def _actual_directory_path(descriptor: int) -> pathlib.Path:
    target = os.readlink(_descriptor_path(descriptor))
    if target.endswith(" (deleted)"):
        raise RuntimeError("sealed evidence directory was removed")
    return pathlib.Path(target)


def _require_absent_output(parent_descriptor: int, name: str) -> None:
    try:
        os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return
    raise RuntimeError("sealed evidence archive already exists")


def _require_output_outside_root(
    root_descriptor: int,
    parent_descriptor: int,
    name: str,
) -> None:
    root = _actual_directory_path(root_descriptor)
    target = _actual_directory_path(parent_descriptor) / name
    try:
        target.relative_to(root)
    except ValueError:
        return
    raise RuntimeError("sealed evidence archive must be outside the evidence tree")


def _archive_name(root: pathlib.Path, path: pathlib.Path) -> str:
    relative = path.relative_to(root)
    return (ARCHIVE_ROOT / pathlib.PurePosixPath(*relative.parts)).as_posix()


def _same_file(left: Any, right: Any) -> bool:
    return left.st_dev == right.st_dev and left.st_ino == right.st_ino


def _remove_created_archive(
    parent_descriptor: int,
    name: str,
    created: Any | None,
) -> None:
    if created is None:
        return
    try:
        current = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return
    if _same_file(created, current):
        os.unlink(name, dir_fd=parent_descriptor)
        os.fsync(parent_descriptor)


def seal_tree(root: pathlib.Path, archive: pathlib.Path) -> tuple[int, int]:
    SANITIZER._require_safe_name(archive)
    root_descriptor = _open_stable_directory(root, "evidence root")
    parent_descriptor = -1
    archive_descriptor = -1
    created_metadata: Any | None = None
    try:
        parent_descriptor = _open_stable_directory(
            archive.parent,
            "sealed evidence parent",
        )
        _require_absent_output(parent_descriptor, archive.name)
        _require_output_outside_root(
            root_descriptor,
            parent_descriptor,
            archive.name,
        )

        root_view = _descriptor_path(root_descriptor)
        paths = sorted(
            SANITIZER._regular_files(root_view),
            key=lambda path: os.fsencode(str(path.relative_to(root_view))),
        )
        if not paths:
            raise RuntimeError("evidence tree contains no regular files")

        archive_descriptor = os.open(
            archive.name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | os.O_CLOEXEC
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=parent_descriptor,
        )
        os.fchmod(archive_descriptor, 0o600)
        created_metadata = os.fstat(archive_descriptor)
        if not stat.S_ISREG(created_metadata.st_mode) or created_metadata.st_nlink != 1:
            raise RuntimeError("sealed evidence archive creation is unsafe")

        total_bytes = 0
        with os.fdopen(os.dup(archive_descriptor), "wb") as output:
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
                    member = tarfile.TarInfo(_archive_name(root_view, path))
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
        os.fsync(archive_descriptor)

        final_descriptor_metadata = os.fstat(archive_descriptor)
        final_path_metadata = os.stat(
            archive.name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISREG(final_descriptor_metadata.st_mode)
            or final_descriptor_metadata.st_nlink != 1
            or stat.S_IMODE(final_descriptor_metadata.st_mode) != 0o600
            or not _same_file(final_descriptor_metadata, final_path_metadata)
        ):
            raise RuntimeError("sealed evidence archive metadata is unsafe")
        os.fsync(parent_descriptor)
        return len(paths), total_bytes
    except Exception:
        if parent_descriptor >= 0:
            _remove_created_archive(
                parent_descriptor,
                archive.name,
                created_metadata,
            )
        raise
    finally:
        if archive_descriptor >= 0:
            os.close(archive_descriptor)
        if parent_descriptor >= 0:
            os.close(parent_descriptor)
        os.close(root_descriptor)


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
