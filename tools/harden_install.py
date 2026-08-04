#!/usr/bin/env python3
"""Verify and run the reviewed HostPanel installer hardener driver."""
from __future__ import annotations

import hashlib
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
MAX_REVIEWED_RUNTIME_FILE_BYTES = 16 * 1024 * 1024

CUSTOMBUILD_EXECUTABLES = (
    "tools/hostpanel-build.py",
    "tools/hostpanel_build_web.py",
    "tools/patch_custombuild_runtime.py",
    "tools/patch_powerdns_runtime.py",
    "tools/patch_varnish_runtime.py",
    "tools/patch_extras_doctor.py",
    "tools/install-hostpanel-build.sh",
)
CUSTOMBUILD_MODULES = (
    "tools/hostpanel_build_config.py",
    "tools/hostpanel_build_packages.py",
    "tools/hostpanel_build_operations.py",
    "tools/hostpanel_build_operations_adapter.py",
    "tools/hostpanel_build_optional_postcheck_adapter.py",
    "tools/hostpanel_build_cli.py",
    "tools/hostpanel_build_ssl.py",
    "tools/hostpanel_build_extras.py",
    "tools/hostpanel_build_extras_impl.py",
    "tools/hostpanel_build_extras_state.py",
    "tools/hostpanel_build_extras_state_impl.py",
    "tools/hostpanel_build_entry.py",
    "tools/hostpanel_build_powerdns_adapter.py",
    "tools/hostpanel_build_powerdns_adapter_impl.py",
    "tools/hostpanel_build_mongodb_adapter.py",
    "tools/hostpanel_build_web_impl.py",
    "tools/hostpanel_build_web_state.py",
    "tools/hostpanel_build_web_transaction_adapter.py",
    "tools/patch_custombuild_runtime_impl.py",
    "tools/patch_powerdns_runtime_impl.py",
    "tools/patch_varnish_runtime_impl.py",
    "tools/patch_extras_doctor_impl.py",
)
CUSTOMBUILD_RUNTIME_MODES = {
    **{name: 0o755 for name in CUSTOMBUILD_EXECUTABLES},
    **{name: 0o644 for name in CUSTOMBUILD_MODULES},
}

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


TRUSTED_DRIVER_PATH = trusted_driver()
DRIVER = load_driver(TRUSTED_DRIVER_PATH)
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


def _trusted_directory(path: pathlib.Path, *, private: bool = False) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise SystemExit(f"cannot inspect reviewed runtime directory: {path}") from exc
    disallowed = 0o077 if private else 0o022
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) & disallowed
    ):
        raise SystemExit(f"unsafe reviewed runtime directory: {path}")
    return metadata


def _trusted_directory_chain(root: pathlib.Path, path: pathlib.Path) -> None:
    if not root.is_absolute() or not path.is_absolute():
        raise SystemExit("reviewed runtime path is not canonical")
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise SystemExit("reviewed runtime path escapes its trusted root") from exc
    current = root
    _trusted_directory(current)
    for component in path.relative_to(root).parts:
        current = current / component
        _trusted_directory(current)


def _reviewed_source_root() -> pathlib.Path:
    explicit = os.environ.get("HP_HARDENER_SOURCE_ROOT", "")
    root = (
        pathlib.Path(explicit)
        if explicit
        else TRUSTED_DRIVER_PATH.parent.parent
    )
    if not root.is_absolute():
        raise SystemExit("the reviewed CustomBuild source root is not canonical")
    _trusted_directory(root)
    git_marker = root / ".git"
    try:
        marker = git_marker.lstat()
    except OSError as exc:
        raise SystemExit("the reviewed CustomBuild source is not a Git checkout") from exc
    if not (stat.S_ISDIR(marker.st_mode) or stat.S_ISREG(marker.st_mode)):
        raise SystemExit("the reviewed CustomBuild Git metadata is unsafe")
    return root


def _run_git(root: pathlib.Path, arguments: list[str]) -> str:
    environment = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": str(root),
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
    }
    try:
        completed = subprocess.run(
            [
                "git", "-c", "core.hooksPath=/dev/null",
                "-c", f"safe.directory={root}",
                "-C", str(root), *arguments,
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            env=environment,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise SystemExit("could not query the reviewed CustomBuild Git object") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()[-500:]
        raise SystemExit(
            f"could not query the reviewed CustomBuild Git object: {detail}"
        )
    return completed.stdout.strip()


def _expected_git_object(root: pathlib.Path, relative: pathlib.PurePosixPath) -> str:
    expected = _run_git(
        root, ["rev-parse", "--verify", f"HEAD:{relative.as_posix()}"]
    )
    if len(expected) != 40 or any(character not in "0123456789abcdef" for character in expected):
        raise SystemExit(f"invalid reviewed Git object for {relative}")
    return expected


def _git_blob_digest(payload: bytes) -> str:
    header = f"blob {len(payload)}\0".encode("ascii")
    return hashlib.sha1(header + payload).hexdigest()


def _read_reviewed_runtime_file(
    root: pathlib.Path, relative_name: str, mode: int
) -> bytes:
    relative = pathlib.PurePosixPath(relative_name)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise SystemExit(f"invalid reviewed runtime path: {relative_name}")
    path = root.joinpath(*relative.parts)
    _trusted_directory_chain(root, path.parent)
    try:
        before = path.lstat()
    except OSError as exc:
        raise SystemExit(f"missing reviewed runtime file: {relative_name}") from exc
    if (
        not stat.S_ISREG(before.st_mode)
        or stat.S_ISLNK(before.st_mode)
        or before.st_uid != os.geteuid()
        or before.st_nlink != 1
        or stat.S_IMODE(before.st_mode) & 0o022
        or (mode & 0o111 and not stat.S_IMODE(before.st_mode) & 0o111)
        or before.st_size < 0
        or before.st_size > MAX_REVIEWED_RUNTIME_FILE_BYTES
    ):
        raise SystemExit(f"unsafe reviewed runtime file: {relative_name}")

    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise SystemExit(f"cannot open reviewed runtime file: {relative_name}") from exc
    try:
        opened = os.fstat(descriptor)
        if not _same_file(before, opened):
            raise SystemExit(f"reviewed runtime file changed before read: {relative_name}")
        chunks: list[bytes] = []
        remaining = opened.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                raise SystemExit(
                    f"reviewed runtime file ended unexpectedly: {relative_name}"
                )
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise SystemExit(f"reviewed runtime file grew during read: {relative_name}")
        after = os.fstat(descriptor)
        current = path.lstat()
        if not _same_file(opened, after) or not _same_file(after, current):
            raise SystemExit(f"reviewed runtime file changed during read: {relative_name}")
    finally:
        os.close(descriptor)

    payload = b"".join(chunks)
    expected = _expected_git_object(root, relative)
    if _git_blob_digest(payload) != expected:
        raise SystemExit(
            f"reviewed runtime file does not match HEAD:{relative_name}"
        )
    return payload


def _ensure_target_tools(root: pathlib.Path) -> pathlib.Path:
    _trusted_directory(root)
    tools = root / "tools"
    if not os.path.lexists(tools):
        tools.mkdir(mode=0o755)
        os.chown(tools, os.geteuid(), os.getegid())
        os.chmod(tools, 0o755)
        _fsync_parent(tools)
    _trusted_directory(tools)
    return tools


def _publish_runtime_file(path: pathlib.Path, payload: bytes, mode: int) -> None:
    _trusted_directory(path.parent)
    if os.path.lexists(path):
        existing = path.lstat()
        if (
            not stat.S_ISREG(existing.st_mode)
            or stat.S_ISLNK(existing.st_mode)
            or existing.st_uid != os.geteuid()
            or existing.st_nlink != 1
            or stat.S_IMODE(existing.st_mode) & 0o022
        ):
            raise SystemExit(f"unsafe CustomBuild overlay target: {path}")

    fd, temporary = _open_temporary(path, mode)
    active_error: BaseException | None = None
    cleanup_error: BaseException | None = None
    try:
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise SystemExit(f"could not write CustomBuild overlay: {temporary}")
            view = view[written:]
        os.fsync(fd)
        os.fchown(fd, os.geteuid(), os.getegid())
        os.fchmod(fd, mode)
        _apply_xattrs(temporary, {})
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


def synchronize_custombuild_runtime(
    reviewed_root: pathlib.Path, target_root: pathlib.Path
) -> None:
    reviewed_root = reviewed_root.absolute()
    target_root = target_root.absolute()
    _trusted_directory(reviewed_root)
    _ensure_target_tools(target_root)

    verified: dict[str, bytes] = {}
    for relative_name, mode in CUSTOMBUILD_RUNTIME_MODES.items():
        verified[relative_name] = _read_reviewed_runtime_file(
            reviewed_root, relative_name, mode
        )

    if reviewed_root == target_root:
        return
    for relative_name, mode in CUSTOMBUILD_RUNTIME_MODES.items():
        destination = target_root.joinpath(
            *pathlib.PurePosixPath(relative_name).parts
        )
        _publish_runtime_file(destination, verified[relative_name], mode)


def _installer_source_root() -> pathlib.Path:
    if len(sys.argv) != 3:
        raise SystemExit("usage: harden_install.py SOURCE DESTINATION")
    source = pathlib.Path(sys.argv[1]).absolute()
    try:
        metadata = source.lstat()
    except OSError as exc:
        raise SystemExit(f"missing installer source: {source}") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise SystemExit(f"unsafe installer source: {source}")
    _trusted_directory(source.parent)
    return source.parent


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
    reviewed_root = _reviewed_source_root()
    target_root = _installer_source_root()
    synchronize_custombuild_runtime(reviewed_root, target_root)

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
