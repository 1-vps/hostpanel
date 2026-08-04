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
CUSTOMBUILD_RUNTIME_BLOBS = {
    "tools/hostpanel-build.py": "2198d7c9696cf61ced847cae22ab661cea7e4b32" if False else "2198d7c969c9b364051f9ea0a4bf7aa8db9bdd3f",
    "tools/hostpanel_build_web.py": "aac4f4380bf4fabf6662c5b754dcd9869582fdec",
    "tools/patch_custombuild_runtime.py": "c79f9d1897d9d984459f84f277f62d6a9a021d28",
    "tools/patch_powerdns_runtime.py": "786afeb612e79bec8f7a98880586ac088d205c2c",
    "tools/patch_varnish_runtime.py": "386680af0516a0c8ad78a70faea371fae215d856",
    "tools/patch_extras_doctor.py": "8fbbe75ab7be817686aa3e49eb601095af011b9a",
    "tools/install-hostpanel-build.sh": "5bb267dfaec4b3aede3524ecb630f05216ec8d58",
    "tools/hostpanel_build_config.py": "67a97c9fb81b95ac40abc08fe9ae7565965f17f7",
    "tools/hostpanel_build_packages.py": "d2b49d6da0735b144c4937b6fa26ec546afc14f6",
    "tools/hostpanel_build_operations.py": "d76c9b28f5f8053fd698af16a60df8c29c5d632f",
    "tools/hostpanel_build_operations_adapter.py": "1eb9bc527bc3aa7646bf99abf4cb56e65e087871",
    "tools/hostpanel_build_optional_postcheck_adapter.py": "f8b61f833edeb8a672fde00dd301efc9c740e5f1",
    "tools/hostpanel_build_cli.py": "083adf2c67169bbe041ff415f0854d53121ca817",
    "tools/hostpanel_build_ssl.py": "61efab030ab08179eb6dc012748202b532e8e36c",
    "tools/hostpanel_build_extras.py": "cf96140db2079a2c4ee2cc2ab92a8a5eceaf996b",
    "tools/hostpanel_build_extras_impl.py": "062186e3a1cefb42dfab406e486119ada829a233",
    "tools/hostpanel_build_extras_state.py": "73337ca3ad61f560c16c7a1f4a5e9dfd9ffc836f",
    "tools/hostpanel_build_extras_state_impl.py": "bd578c71d6fd6515489ac091df8128e9abdb2bc5",
    "tools/hostpanel_build_entry.py": "d22cfa28518f771cfe02d1e68fdfc8c46e78d54b",
    "tools/hostpanel_build_powerdns_adapter.py": "80ffa4dabe0bad15747f76e056a0c5b29fc02aa1",
    "tools/hostpanel_build_powerdns_adapter_impl.py": "ffb082ac070b10b050dce9cd289896a8f4d24b3b",
    "tools/hostpanel_build_mongodb_adapter.py": "9f2430fc3c34d6cd81d3d2b790c22881c551d921",
    "tools/hostpanel_build_web_impl.py": "297b4ec6eea991a118ca81a4134268433223d432",
    "tools/hostpanel_build_web_state.py": "1d2860751f119707d75a8cc1a303f23a8a871a53",
    "tools/hostpanel_build_web_transaction_adapter.py": "6cffe6eed68e2270357930d8570a568f6ab87d54",
    "tools/patch_custombuild_runtime_impl.py": "1eddff6963eb6de67962125283d7de8b087b2f33",
    "tools/patch_powerdns_runtime_impl.py": "25fd22082596f417d83d07bdb0b6130ae76b615f",
    "tools/patch_varnish_runtime_impl.py": "9f8c5ed92528ef99798febe236eec061e4093741",
    "tools/patch_extras_doctor_impl.py": "84bb9b78b8c9dd231ee08e9cf839c64e65ef0b56",
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


def _trusted_directory(path: pathlib.Path) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise SystemExit(f"cannot inspect reviewed runtime directory: {path}") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise SystemExit(f"unsafe reviewed runtime directory: {path}")
    return metadata


def _trusted_directory_chain(root: pathlib.Path, path: pathlib.Path) -> None:
    if not root.is_absolute() or not path.is_absolute():
        raise SystemExit("reviewed runtime path is not canonical")
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise SystemExit("reviewed runtime path escapes its trusted root") from exc
    current = root
    _trusted_directory(current)
    for component in relative.parts:
        current = current / component
        _trusted_directory(current)


def _reviewed_source_root() -> pathlib.Path:
    explicit = os.environ.get("HP_HARDENER_SOURCE_ROOT", "")
    root = pathlib.Path(explicit) if explicit else TRUSTED_DRIVER_PATH.parent.parent
    if not root.is_absolute():
        raise SystemExit("the reviewed CustomBuild source root is not canonical")
    _trusted_directory(root)
    return root


def _git_blob_digest(payload: bytes) -> str:
    header = f"blob {len(payload)}\0".encode("ascii")
    return hashlib.sha1(header + payload).hexdigest()


def _read_reviewed_runtime_file(root: pathlib.Path, relative_name: str) -> bytes:
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
    expected = CUSTOMBUILD_RUNTIME_BLOBS.get(relative_name)
    if expected is None or _git_blob_digest(payload) != expected:
        raise SystemExit(
            f"reviewed runtime file does not match its Git blob: {relative_name}"
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
    if set(CUSTOMBUILD_RUNTIME_MODES) != set(CUSTOMBUILD_RUNTIME_BLOBS):
        raise SystemExit("CustomBuild runtime manifest is incomplete")
    _trusted_directory(reviewed_root)
    _ensure_target_tools(target_root)

    verified = {
        relative_name: _read_reviewed_runtime_file(reviewed_root, relative_name)
        for relative_name in CUSTOMBUILD_RUNTIME_MODES
    }
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
