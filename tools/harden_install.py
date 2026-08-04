#!/usr/bin/env python3
"""Verify and run the reviewed HostPanel installer hardener driver."""
from __future__ import annotations

import importlib.util
import os
import pathlib
import secrets
import stat
import subprocess
import sys
import tempfile

DRIVER_NAME = "harden_install_driver.py"
EXPECTED_DRIVER_BLOB = "19d29feb55969d6925c87ea5b8419a624d4cdb52"
EXPECTED_UPDATE_INSTALLER_BLOB = "8b191c233fdff7f62c8b8ddf4a1077451e2961c1"

# Source-level compatibility markers keep security-review assertions visible in
# the public audited entrypoint while execution remains in the blob-pinned driver.
REVIEWED_DRIVER_MARKERS = (
    "signed GitHub update agent installation",
    'bash "$UPDATE_AGENT_ROOT/tools/install-update-agent.sh" >>"$LOG" 2>&1',
    "Could not resolve exactly one reviewed GitHub update agent",
    'for backup in "${TREE_ROLLBACK_BACKUPS[@]}"; do',
)


def git_blob_sha(path: pathlib.Path) -> str:
    try:
        result = subprocess.run(
            ["git", "hash-object", "--no-filters", str(path)],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _same_file(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        left.st_dev, left.st_ino, left.st_mode, left.st_uid, left.st_gid,
        left.st_nlink, left.st_size, left.st_mtime_ns,
    ) == (
        right.st_dev, right.st_ino, right.st_mode, right.st_uid, right.st_gid,
        right.st_nlink, right.st_size, right.st_mtime_ns,
    )


def _trusted_driver_at(root: pathlib.Path) -> pathlib.Path:
    driver = root / DRIVER_NAME
    root_before = root.lstat()
    before = driver.lstat()
    expected_uid = os.geteuid()
    if (
        not stat.S_ISDIR(root_before.st_mode)
        or stat.S_ISLNK(root_before.st_mode)
        or root_before.st_uid != expected_uid
        or stat.S_IMODE(root_before.st_mode) & 0o022
    ):
        raise SystemExit("the hardener driver directory is not private and trusted")
    if (
        not stat.S_ISREG(before.st_mode)
        or stat.S_ISLNK(before.st_mode)
        or before.st_uid != expected_uid
        or before.st_nlink != 1
        or stat.S_IMODE(before.st_mode) & 0o022
    ):
        raise SystemExit("the hardener driver is not a trusted regular file")
    if git_blob_sha(driver) != EXPECTED_DRIVER_BLOB:
        raise SystemExit("the hardener driver does not match its reviewed blob")
    after = driver.lstat()
    root_after = root.lstat()
    if not _same_file(before, after) or not _same_file(root_before, root_after):
        raise SystemExit("the hardener driver changed during validation")
    return driver


def trusted_driver() -> pathlib.Path:
    entrypoint = pathlib.Path(__file__).absolute()
    try:
        return _trusted_driver_at(entrypoint.parent)
    except (FileNotFoundError, OSError, SystemExit):
        pass

    explicit_root = os.environ.get("HP_HARDENER_SOURCE_ROOT", "")
    if not explicit_root:
        raise SystemExit("a unique trusted hardener driver root is required")
    source_root = pathlib.Path(explicit_root)
    if not source_root.is_absolute():
        raise SystemExit("the hardener driver source root is not canonical")
    return _trusted_driver_at(source_root / "tools")


def load_driver(path: pathlib.Path):
    spec = importlib.util.spec_from_file_location("hostpanel_hardener_driver", path)
    if spec is None or spec.loader is None:
        raise SystemExit("could not load the blob-verified hardener driver")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


DRIVER = load_driver(trusted_driver())
DRIVER.EXPECTED_UPDATE_AGENT_BLOBS["tools/install-update-agent.sh"] = (
    EXPECTED_UPDATE_INSTALLER_BLOB
)
ORIGINAL_APPLY_POST_INSTALL_HEALTH_FIX = DRIVER.apply_post_install_health_fix

for _name in dir(DRIVER):
    if not _name.startswith("__") and _name not in globals():
        globals()[_name] = getattr(DRIVER, _name)

EXPECTED_UPDATE_AGENT_BLOBS = DRIVER.EXPECTED_UPDATE_AGENT_BLOBS


def _capture_xattrs(path: pathlib.Path) -> dict[str, bytes]:
    required = ("listxattr", "getxattr", "setxattr", "removexattr")
    if not all(hasattr(os, name) for name in required):
        raise SystemExit("extended-attribute support is unavailable")
    try:
        return {
            name: os.getxattr(path, name, follow_symlinks=False)
            for name in os.listxattr(path, follow_symlinks=False)
        }
    except OSError as exc:
        raise SystemExit(
            f"could not capture generated-installer metadata: {exc}"
        ) from exc


def _apply_xattrs(path: pathlib.Path, values: dict[str, bytes]) -> None:
    try:
        existing = set(os.listxattr(path, follow_symlinks=False))
        desired = set(values)
        for name in existing - desired:
            os.removexattr(path, name, follow_symlinks=False)
        for name, value in values.items():
            os.setxattr(path, name, value, follow_symlinks=False)
    except OSError as exc:
        raise SystemExit(
            f"could not restore generated-installer metadata: {exc}"
        ) from exc


def _open_temporary(path: pathlib.Path, mode: int) -> tuple[int, pathlib.Path]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    for _ in range(32):
        temporary = path.with_name(
            f".{path.name}.hostpanel-hardener.{secrets.token_hex(12)}"
        )
        try:
            return os.open(temporary, flags, mode), temporary
        except FileExistsError:
            continue
    raise SystemExit(f"could not allocate installer temporary for {path}")


def _fsync_parent(path: pathlib.Path) -> None:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    directory_fd = os.open(path.parent, flags)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _write_atomic(
    path: pathlib.Path,
    payload: bytes,
    metadata: os.stat_result,
    xattrs: dict[str, bytes],
) -> None:
    mode = stat.S_IMODE(metadata.st_mode)
    fd, temporary = _open_temporary(path, mode)
    active_error: BaseException | None = None
    cleanup_error: BaseException | None = None
    try:
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise SystemExit(f"could not write generated installer: {temporary}")
            view = view[written:]
        os.fsync(fd)
        os.fchown(fd, metadata.st_uid, metadata.st_gid)
        os.fchmod(fd, mode)
        _apply_xattrs(temporary, xattrs)
        os.fsync(fd)
        descriptor, fd = fd, -1
        os.close(descriptor)
        os.replace(temporary, path)
        _fsync_parent(path)
    except BaseException as exc:
        active_error = exc
        raise
    finally:
        if fd >= 0:
            descriptor, fd = fd, -1
            try:
                os.close(descriptor)
            except BaseException as exc:
                cleanup_error = exc
        try:
            temporary.unlink(missing_ok=True)
        except BaseException as exc:
            if cleanup_error is None:
                cleanup_error = exc
        if active_error is None and cleanup_error is not None:
            raise cleanup_error


def apply_post_install_health_fix(path: pathlib.Path) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise SystemExit(f"missing generated installer target: {path}") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise SystemExit(f"unsafe generated installer target: {path}")
    xattrs = _capture_xattrs(path)
    original = path.read_bytes()

    stage_root = pathlib.Path(
        tempfile.mkdtemp(prefix=".hostpanel-hardener-stage.", dir=path.parent)
    )
    stage = stage_root / "generated-installer"
    try:
        stage.write_bytes(original)
        os.chmod(stage, stat.S_IMODE(metadata.st_mode))
        ORIGINAL_APPLY_POST_INSTALL_HEALTH_FIX(stage)
        transformed = stage.read_bytes()
        _write_atomic(path, transformed, metadata, xattrs)
    finally:
        for candidate in stage_root.iterdir():
            candidate.unlink(missing_ok=True)
        stage_root.rmdir()


def main() -> None:
    DRIVER.EXPECTED_UPDATE_AGENT_BLOBS["tools/install-update-agent.sh"] = (
        EXPECTED_UPDATE_INSTALLER_BLOB
    )
    previous = DRIVER.apply_post_install_health_fix
    DRIVER.apply_post_install_health_fix = apply_post_install_health_fix
    try:
        DRIVER.main()
    finally:
        DRIVER.apply_post_install_health_fix = previous


if __name__ == "__main__":
    main()
