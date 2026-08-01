#!/usr/bin/env python3
from __future__ import annotations

import os
import stat
import sys


ARTIFACTS_DIRECTORY = "artifacts"
EVIDENCE_DIRECTORY = "qemu-vm-acceptance"
MARKER_NAME = "runner-evidence-state.txt"
MARKER_CONTENT = b"No VM evidence was produced before the always-run sealing step.\n"
DIRECTORY_MODE = 0o700
FILE_MODE = 0o600


def _validate_directory(metadata: os.stat_result, label: str) -> None:
    if not stat.S_ISDIR(metadata.st_mode):
        raise RuntimeError(f"{label} is not a directory")
    if metadata.st_uid != os.getuid() or metadata.st_gid != os.getgid():
        raise RuntimeError(f"{label} is not owned by the runner identity")


def _open_directory(name: str, *, dir_fd: int | None = None) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    return os.open(name, flags, dir_fd=dir_fd)


def _write_all(descriptor: int, payload: bytes) -> None:
    remaining = memoryview(payload)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise RuntimeError("could not write the QEMU evidence marker")
        remaining = remaining[written:]


def prepare() -> None:
    try:
        os.mkdir(ARTIFACTS_DIRECTORY, DIRECTORY_MODE)
    except FileExistsError:
        pass

    parent_fd = _open_directory(ARTIFACTS_DIRECTORY)
    try:
        _validate_directory(os.fstat(parent_fd), "artifact directory")
        os.fchmod(parent_fd, DIRECTORY_MODE)

        try:
            os.mkdir(EVIDENCE_DIRECTORY, DIRECTORY_MODE, dir_fd=parent_fd)
        except FileExistsError:
            pass

        evidence_fd = _open_directory(EVIDENCE_DIRECTORY, dir_fd=parent_fd)
        try:
            _validate_directory(os.fstat(evidence_fd), "QEMU evidence directory")
            os.fchmod(evidence_fd, DIRECTORY_MODE)

            if os.listdir(evidence_fd):
                return

            marker_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
            marker_fd = os.open(
                MARKER_NAME,
                marker_flags,
                FILE_MODE,
                dir_fd=evidence_fd,
            )
            try:
                os.fchmod(marker_fd, FILE_MODE)
                _write_all(marker_fd, MARKER_CONTENT)
                os.fsync(marker_fd)
            finally:
                os.close(marker_fd)
        finally:
            os.close(evidence_fd)
    finally:
        os.close(parent_fd)


def main() -> int:
    try:
        prepare()
    except RuntimeError as error:
        print(f"QEMU evidence preparation failed: {error}", file=sys.stderr)
        return 1
    except OSError as error:
        errno = error.errno if error.errno is not None else "unknown"
        print(
            f"QEMU evidence preparation failed: filesystem error ({errno})",
            file=sys.stderr,
        )
        return 1
    print("QEMU evidence preparation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
