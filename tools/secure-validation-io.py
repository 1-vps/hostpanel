#!/usr/bin/env python3
"""Descriptor-bound I/O and hook execution for HostPanel production validation."""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import os
import pathlib
import secrets
import stat
import subprocess
import sys
from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

MAX_STATE_BYTES = 4096
MAX_HOOK_BYTES = 16 * 1024 * 1024
REPORT_PREFIX = "hostpanel-production-validation-"
SAFE_PATH = "/usr/sbin:/usr/bin:/sbin:/bin"
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
FILE_READ_FLAGS = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW


class ValidationIoError(RuntimeError):
    """Raised when a privileged validation path is unsafe or changes."""


@dataclass
class TrustedDirectory:
    path: pathlib.Path
    fd: int
    stat_result: os.stat_result

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1

    def __enter__(self) -> "TrustedDirectory":
        return self

    def __exit__(self, _type, _value, _traceback) -> None:
        self.close()


def _mode_bits(metadata: os.stat_result) -> int:
    return stat.S_IMODE(metadata.st_mode)


def _same_inode(left: os.stat_result, right: os.stat_result) -> bool:
    return left.st_dev == right.st_dev and left.st_ino == right.st_ino


def _validate_directory_metadata(
    metadata: os.stat_result,
    label: str,
    *,
    owner_uid: int,
    exact_mode: int | None = None,
) -> None:
    if not stat.S_ISDIR(metadata.st_mode):
        raise ValidationIoError(f"{label} is not a directory")
    if metadata.st_uid != owner_uid:
        raise ValidationIoError(f"{label} is not owned by the trusted user")
    mode = _mode_bits(metadata)
    if mode & 0o022:
        raise ValidationIoError(f"{label} is group/world writable")
    if exact_mode is not None and mode != exact_mode:
        raise ValidationIoError(
            f"{label} mode is {mode:04o}, expected {exact_mode:04o}"
        )


def _normalize_absolute(path: pathlib.Path) -> pathlib.Path:
    if not path.is_absolute():
        raise ValidationIoError(f"path must be absolute: {path}")
    if any(part in ("", ".", "..") for part in path.parts[1:]):
        raise ValidationIoError(f"path contains an unsafe component: {path}")
    return path


def _relative_parts(path: pathlib.Path, trusted_root: pathlib.Path) -> tuple[str, ...]:
    path = _normalize_absolute(path)
    trusted_root = _normalize_absolute(trusted_root)
    try:
        relative = path.relative_to(trusted_root)
    except ValueError as exc:
        raise ValidationIoError(
            f"path is outside the trusted root {trusted_root}: {path}"
        ) from exc
    return tuple(relative.parts)


def _verify_path_matches_directory(directory: TrustedDirectory) -> None:
    try:
        current = os.stat(directory.path, follow_symlinks=False)
    except OSError as exc:
        raise ValidationIoError(
            f"trusted directory path became unavailable: {directory.path}"
        ) from exc
    if not _same_inode(directory.stat_result, current) or not stat.S_ISDIR(
        current.st_mode
    ):
        raise ValidationIoError(f"trusted directory path changed: {directory.path}")


def open_trusted_directory(
    path: pathlib.Path,
    *,
    owner_uid: int = 0,
    exact_mode: int | None = None,
    create: bool = False,
    trusted_root: pathlib.Path = pathlib.Path("/"),
) -> TrustedDirectory:
    path = _normalize_absolute(path)
    trusted_root = _normalize_absolute(trusted_root)
    parts = _relative_parts(path, trusted_root)
    root_fd = os.open(trusted_root, DIRECTORY_FLAGS)
    current_fd = root_fd
    current_path = trusted_root
    try:
        root_metadata = os.fstat(current_fd)
        _validate_directory_metadata(
            root_metadata,
            str(current_path),
            owner_uid=owner_uid,
            exact_mode=exact_mode if not parts else None,
        )
        if not parts:
            return TrustedDirectory(path, current_fd, root_metadata)

        for index, part in enumerate(parts):
            final = index == len(parts) - 1
            try:
                next_fd = os.open(part, DIRECTORY_FLAGS, dir_fd=current_fd)
            except FileNotFoundError:
                if not (create and final):
                    raise ValidationIoError(f"trusted directory is missing: {path}")
                os.mkdir(
                    part,
                    exact_mode if exact_mode is not None else 0o700,
                    dir_fd=current_fd,
                )
                next_fd = os.open(part, DIRECTORY_FLAGS, dir_fd=current_fd)
            except OSError as exc:
                raise ValidationIoError(
                    f"could not open trusted directory component: {current_path / part}"
                ) from exc
            if current_fd != root_fd:
                os.close(current_fd)
            current_fd = next_fd
            current_path = current_path / part
            metadata = os.fstat(current_fd)
            _validate_directory_metadata(
                metadata,
                str(current_path),
                owner_uid=owner_uid,
                exact_mode=exact_mode if final else None,
            )
        return TrustedDirectory(path, current_fd, os.fstat(current_fd))
    except BaseException:
        if current_fd >= 0:
            os.close(current_fd)
        if root_fd >= 0 and root_fd != current_fd:
            os.close(root_fd)
        raise
    finally:
        if root_fd >= 0 and root_fd != current_fd:
            try:
                os.close(root_fd)
            except OSError:
                pass


def ensure_directory(
    path: pathlib.Path,
    mode: int,
    *,
    owner_uid: int = 0,
    trusted_root: pathlib.Path = pathlib.Path("/"),
) -> None:
    with open_trusted_directory(
        path,
        owner_uid=owner_uid,
        exact_mode=mode,
        create=True,
        trusted_root=trusted_root,
    ) as directory:
        _verify_path_matches_directory(directory)
        os.fsync(directory.fd)


def _validate_regular_file(
    metadata: os.stat_result,
    label: str,
    *,
    owner_uid: int,
    exact_mode: int | None = None,
    require_executable: bool = False,
    maximum_size: int | None = None,
) -> None:
    if not stat.S_ISREG(metadata.st_mode):
        raise ValidationIoError(f"{label} is not a regular file")
    if metadata.st_uid != owner_uid:
        raise ValidationIoError(f"{label} is not owned by the trusted user")
    if metadata.st_nlink != 1:
        raise ValidationIoError(f"{label} must have exactly one hard link")
    mode = _mode_bits(metadata)
    if mode & 0o022:
        raise ValidationIoError(f"{label} is group/world writable")
    if exact_mode is not None and mode != exact_mode:
        raise ValidationIoError(
            f"{label} mode is {mode:04o}, expected {exact_mode:04o}"
        )
    if require_executable and mode & 0o111 == 0:
        raise ValidationIoError(f"{label} is not executable")
    if maximum_size is not None and metadata.st_size > maximum_size:
        raise ValidationIoError(f"{label} exceeds the size limit")


def _open_parent(
    path: pathlib.Path,
    *,
    owner_uid: int,
    trusted_root: pathlib.Path,
) -> tuple[TrustedDirectory, str]:
    path = _normalize_absolute(path)
    if path.name in ("", ".", ".."):
        raise ValidationIoError(f"unsafe final path component: {path}")
    parent = open_trusted_directory(
        path.parent,
        owner_uid=owner_uid,
        trusted_root=trusted_root,
    )
    return parent, path.name


def _stat_at(parent_fd: int, name: str) -> os.stat_result | None:
    try:
        return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _validate_existing_state(
    parent_fd: int,
    name: str,
    *,
    owner_uid: int,
) -> os.stat_result | None:
    metadata = _stat_at(parent_fd, name)
    if metadata is None:
        return None
    _validate_regular_file(
        metadata,
        f"state file {name}",
        owner_uid=owner_uid,
        exact_mode=0o600,
        maximum_size=MAX_STATE_BYTES,
    )
    return metadata


def read_state(
    path: pathlib.Path,
    *,
    owner_uid: int = 0,
    trusted_root: pathlib.Path = pathlib.Path("/"),
) -> bytes:
    parent, name = _open_parent(path, owner_uid=owner_uid, trusted_root=trusted_root)
    with parent:
        try:
            fd = os.open(name, FILE_READ_FLAGS | os.O_NONBLOCK, dir_fd=parent.fd)
        except OSError as exc:
            parent.close()
            raise ValidationIoError(f"could not safely open state file {path}") from exc
        try:
            before = os.fstat(fd)
            _validate_regular_file(
                before,
                f"state file {path}",
                owner_uid=owner_uid,
                exact_mode=0o600,
                maximum_size=MAX_STATE_BYTES,
            )
            chunks: list[bytes] = []
            remaining = MAX_STATE_BYTES + 1
            while remaining:
                chunk = os.read(fd, min(remaining, 4096))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            data = b"".join(chunks)
            if len(data) > MAX_STATE_BYTES:
                raise ValidationIoError("state file exceeds the size limit")
            after = os.fstat(fd)
            if not _same_inode(before, after) or before.st_size != after.st_size:
                raise ValidationIoError("state file changed while it was read")
            current = _stat_at(parent.fd, name)
            if current is None or not _same_inode(before, current):
                raise ValidationIoError("state file path changed while it was read")
            _verify_path_matches_directory(parent)
            return data
        finally:
            os.close(fd)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise ValidationIoError("short write")
        view = view[written:]


def write_state(
    path: pathlib.Path,
    data: bytes,
    *,
    owner_uid: int = 0,
    trusted_root: pathlib.Path = pathlib.Path("/"),
) -> None:
    if not data or len(data) > MAX_STATE_BYTES:
        raise ValidationIoError("state payload is empty or exceeds the size limit")
    parent, name = _open_parent(path, owner_uid=owner_uid, trusted_root=trusted_root)
    with parent:
        existing = _validate_existing_state(parent.fd, name, owner_uid=owner_uid)
        temporary = f".{name}.tmp-{os.getpid()}-{secrets.token_hex(8)}"
        fd = -1
        try:
            fd = os.open(
                temporary,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | os.O_NOFOLLOW
                | os.O_CLOEXEC,
                0o600,
                dir_fd=parent.fd,
            )
            os.fchown(fd, owner_uid, 0 if owner_uid == 0 else os.getgid())
            os.fchmod(fd, 0o600)
            _write_all(fd, data)
            os.fsync(fd)
            staged = os.fstat(fd)
            _validate_regular_file(
                staged,
                "staged state file",
                owner_uid=owner_uid,
                exact_mode=0o600,
                maximum_size=MAX_STATE_BYTES,
            )
            current = _stat_at(parent.fd, name)
            if existing is None:
                if current is not None:
                    raise ValidationIoError("state target appeared before atomic replace")
            elif current is None or not _same_inode(existing, current):
                raise ValidationIoError("state target changed before atomic replace")
            _verify_path_matches_directory(parent)
            os.replace(
                temporary,
                name,
                src_dir_fd=parent.fd,
                dst_dir_fd=parent.fd,
            )
            os.fsync(parent.fd)
            final_fd = os.open(name, FILE_READ_FLAGS, dir_fd=parent.fd)
            try:
                final = os.fstat(final_fd)
                _validate_regular_file(
                    final,
                    f"state file {path}",
                    owner_uid=owner_uid,
                    exact_mode=0o600,
                    maximum_size=MAX_STATE_BYTES,
                )
                if not _same_inode(staged, final):
                    raise ValidationIoError("atomic state replacement changed identity")
            finally:
                os.close(final_fd)
            _verify_path_matches_directory(parent)
        finally:
            if fd >= 0:
                os.close(fd)
            try:
                os.unlink(temporary, dir_fd=parent.fd)
            except FileNotFoundError:
                pass


def open_hook_descriptor(
    path: pathlib.Path,
    *,
    owner_uid: int = 0,
    trusted_root: pathlib.Path = pathlib.Path("/"),
) -> tuple[int, TrustedDirectory, os.stat_result]:
    parent, name = _open_parent(path, owner_uid=owner_uid, trusted_root=trusted_root)
    try:
        fd = os.open(name, FILE_READ_FLAGS | os.O_NONBLOCK, dir_fd=parent.fd)
    except OSError as exc:
        parent.close()
        raise ValidationIoError(f"could not safely open hook {path}") from exc
    try:
        metadata = os.fstat(fd)
        _validate_regular_file(
            metadata,
            f"hook {path}",
            owner_uid=owner_uid,
            require_executable=True,
            maximum_size=MAX_HOOK_BYTES,
        )
        current = _stat_at(parent.fd, name)
        if current is None or not _same_inode(metadata, current):
            raise ValidationIoError("hook path changed while it was opened")
        _verify_path_matches_directory(parent)
        return fd, parent, metadata
    except BaseException:
        os.close(fd)
        parent.close()
        raise


def _hook_environment(source: Mapping[str, str]) -> dict[str, str]:
    environment = {
        "PATH": SAFE_PATH,
        "HOME": "/root",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }
    for key, value in source.items():
        if key.startswith("HP_") and "\x00" not in value:
            environment[key] = value
    return environment


def execute_hook(
    path: pathlib.Path,
    *,
    owner_uid: int = 0,
    trusted_root: pathlib.Path = pathlib.Path("/"),
    environment: Mapping[str, str] | None = None,
    before_exec: Callable[[], None] | None = None,
) -> "NoReturn":
    fd, parent, metadata = open_hook_descriptor(
        path, owner_uid=owner_uid, trusted_root=trusted_root
    )
    high_fd = -1
    try:
        if before_exec is not None:
            before_exec()
        current = _stat_at(parent.fd, path.name)
        if current is None or not _same_inode(metadata, current):
            if before_exec is None:
                raise ValidationIoError("hook path changed before descriptor execution")
        _verify_path_matches_directory(parent)
        high_fd = fcntl.fcntl(fd, fcntl.F_DUPFD_CLOEXEC, 100)
        os.set_inheritable(high_fd, True)
        hook_env = _hook_environment(environment or os.environ)
        if os.execve not in os.supports_fd:
            raise ValidationIoError("descriptor execution is unavailable on this platform")
        os.execve(high_fd, [str(path)], hook_env)
    finally:
        if high_fd >= 0:
            os.close(high_fd)
        os.close(fd)
        parent.close()


def _create_report(
    report_directory: pathlib.Path,
    *,
    owner_uid: int,
    trusted_root: pathlib.Path,
) -> tuple[TrustedDirectory, str, int]:
    directory = open_trusted_directory(
        report_directory,
        owner_uid=owner_uid,
        exact_mode=0o755,
        create=True,
        trusted_root=trusted_root,
    )
    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for _ in range(32):
        name = f"{REPORT_PREFIX}{timestamp}-{os.getpid()}-{secrets.token_hex(6)}.log"
        try:
            fd = os.open(
                name,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | os.O_NOFOLLOW
                | os.O_CLOEXEC,
                0o600,
                dir_fd=directory.fd,
            )
        except FileExistsError:
            continue
        os.fchown(fd, owner_uid, 0 if owner_uid == 0 else os.getgid())
        os.fchmod(fd, 0o600)
        return directory, name, fd
    directory.close()
    raise ValidationIoError("could not allocate a unique validation report")


def run_with_report(
    report_directory: pathlib.Path,
    command: Sequence[str],
    *,
    owner_uid: int = 0,
    trusted_root: pathlib.Path = pathlib.Path("/"),
) -> int:
    if not command or not pathlib.Path(command[0]).is_absolute():
        raise ValidationIoError("report command must use an absolute executable path")
    directory, name, report_fd = _create_report(
        report_directory,
        owner_uid=owner_uid,
        trusted_root=trusted_root,
    )
    report_path = report_directory / name
    try:
        created = os.fstat(report_fd)
        environment = dict(os.environ)
        environment["HP_VALIDATION_REPORT_ACTIVE"] = "yes"
        environment["HP_VALIDATION_REPORT_PATH"] = str(report_path)
        process = subprocess.Popen(
            list(command),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=environment,
            close_fds=True,
        )
        assert process.stdout is not None
        while True:
            chunk = process.stdout.read(65536)
            if not chunk:
                break
            _write_all(report_fd, chunk)
            sys.stdout.buffer.write(chunk)
            sys.stdout.buffer.flush()
        status = process.wait()
        process.stdout.close()
        os.fsync(report_fd)
        final = os.fstat(report_fd)
        _validate_regular_file(
            final,
            f"validation report {report_path}",
            owner_uid=owner_uid,
            exact_mode=0o600,
        )
        if not _same_inode(created, final):
            raise ValidationIoError("validation report descriptor changed")
        current = _stat_at(directory.fd, name)
        if current is None or not _same_inode(final, current):
            raise ValidationIoError("validation report path changed")
        os.fsync(directory.fd)
        _verify_path_matches_directory(directory)
        return status
    finally:
        os.close(report_fd)
        directory.close()


def _parse_mode(raw: str) -> int:
    try:
        mode = int(raw, 8)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("mode must be octal") from exc
    if mode < 0 or mode > 0o777:
        raise argparse.ArgumentTypeError("mode is outside 0000-0777")
    return mode


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    ensure = subparsers.add_parser("ensure-directory")
    ensure.add_argument("path", type=pathlib.Path)
    ensure.add_argument("mode", type=_parse_mode)

    read = subparsers.add_parser("read-state")
    read.add_argument("path", type=pathlib.Path)

    write = subparsers.add_parser("write-state")
    write.add_argument("path", type=pathlib.Path)

    hook = subparsers.add_parser("exec-hook")
    hook.add_argument("path", type=pathlib.Path)

    report = subparsers.add_parser("run-with-report")
    report.add_argument("directory", type=pathlib.Path)
    report.add_argument("remainder", nargs=argparse.REMAINDER)

    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(list(argv) if argv is not None else sys.argv[1:])
    if os.geteuid() != 0:
        raise ValidationIoError("secure validation I/O must run as root")
    if args.command == "ensure-directory":
        ensure_directory(args.path, args.mode)
        return 0
    if args.command == "read-state":
        sys.stdout.buffer.write(read_state(args.path))
        return 0
    if args.command == "write-state":
        payload = sys.stdin.buffer.read(MAX_STATE_BYTES + 1)
        write_state(args.path, payload)
        return 0
    if args.command == "exec-hook":
        execute_hook(args.path)
    if args.command == "run-with-report":
        remainder = list(args.remainder)
        if remainder and remainder[0] == "--":
            remainder.pop(0)
        return run_with_report(args.directory, remainder)
    raise ValidationIoError("unknown command")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValidationIoError as error:
        print(f"Secure validation I/O failed: {error}", file=sys.stderr)
        raise SystemExit(1)
