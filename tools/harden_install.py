#!/usr/bin/env python3
"""Verify and run the reviewed HostPanel installer hardener implementation."""
from __future__ import annotations

import hashlib
import importlib.util
import os
import pathlib
import stat
import sys

DRIVER_NAME = "harden_install_driver.py"
IMPLEMENTATION_NAME = "harden_install_entry_impl.py"
EXPECTED_IMPLEMENTATION_BLOB = "739a29690bd0e938eb291d7d1921af751bd68e1d"
EXPECTED_DRIVER_BLOB = "19d29feb55969d6925c87ea5b8419a624d4cdb52"
EXPECTED_UPDATE_INSTALLER_BLOB = "86fbb83c83bbff7786e6098e5e853c50655cf4ce"
RUNTIME_BLOB_OVERRIDES = {
    "tools/hostpanel_build_cli.py": (
        "5b310214204d47ea5742e27864c543365473eb5b"
    ),
    "tools/hostpanel_build_config.py": (
        "ed84d4a123b1e2ae519ddc09687b4cd91c045fa2"
    ),
    "tools/hostpanel_build_entry.py": (
        "8c8dea5c91a7c47ca736032e4a97c1848b658fde"
    ),
    "tools/hostpanel_build_extras.py": (
        "69873098d3693cd6af3bbd5315e7d65fc829c986"
    ),
    "tools/hostpanel_build_extras_state.py": (
        "c08c003ae65703f9a41d23b5ca5d74c26ad29eeb"
    ),
    "tools/hostpanel_build_powerdns_adapter.py": (
        "efecbd561691c3fd541c522d68fa39861cbe5a52"
    ),
    "tools/hostpanel_build_ssl.py": (
        "f85f28a6cbeff9f1734242eb136b81c46fb75cf1"
    ),
    "tools/hostpanel_build_web_transaction_adapter.py": (
        "40fa79006127fc1a1bc0686ecd93bdd09e287008"
    ),
}

HARDENER_SUPPORT_FILES = {
    f"tools/{IMPLEMENTATION_NAME}": (EXPECTED_IMPLEMENTATION_BLOB, 0o644),
    f"tools/{DRIVER_NAME}": (EXPECTED_DRIVER_BLOB, 0o644),
}

# Keep the security-review contract visible in the public audited entrypoint.
REVIEWED_DRIVER_MARKERS = (
    "signed GitHub update agent installation",
    'bash "$UPDATE_AGENT_ROOT/tools/install-update-agent.sh" >>"$LOG" 2>&1',
    "Could not resolve exactly one reviewed GitHub update agent",
    'for backup in "${TREE_ROLLBACK_BACKUPS[@]}"; do',
)


def _git_blob_digest(payload: bytes) -> str:
    header = f"blob {len(payload)}\0".encode("ascii")
    return hashlib.sha1(header + payload).hexdigest()


def _same_file(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        left.st_dev, left.st_ino, left.st_mode, left.st_uid, left.st_gid,
        left.st_nlink, left.st_size, left.st_mtime_ns,
    ) == (
        right.st_dev, right.st_ino, right.st_mode, right.st_uid, right.st_gid,
        right.st_nlink, right.st_size, right.st_mtime_ns,
    )


def _trusted_regular(path: pathlib.Path, expected_blob: str) -> pathlib.Path:
    root = path.parent
    try:
        root_before = root.lstat()
        before = path.lstat()
    except OSError as exc:
        raise SystemExit(f"cannot inspect hardener support file: {path}") from exc
    expected_uid = os.geteuid()
    if (
        not stat.S_ISDIR(root_before.st_mode)
        or stat.S_ISLNK(root_before.st_mode)
        or root_before.st_uid != expected_uid
        or stat.S_IMODE(root_before.st_mode) & 0o022
    ):
        raise SystemExit("the hardener implementation directory is not trusted")
    if (
        not stat.S_ISREG(before.st_mode)
        or stat.S_ISLNK(before.st_mode)
        or before.st_uid != expected_uid
        or before.st_nlink != 1
        or stat.S_IMODE(before.st_mode) & 0o022
        or before.st_size <= 0
        or before.st_size > 16 * 1024 * 1024
    ):
        raise SystemExit(f"unsafe hardener support file: {path}")

    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise SystemExit(f"cannot open hardener support file: {path}") from exc
    try:
        opened = os.fstat(descriptor)
        if not _same_file(before, opened):
            raise SystemExit(
                f"hardener support file changed before read: {path}"
            )
        chunks: list[bytes] = []
        remaining = opened.st_size
        while remaining:
            try:
                chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            except OSError as exc:
                raise SystemExit(
                    f"cannot read hardener support file: {path}"
                ) from exc
            if not chunk:
                raise SystemExit(
                    f"hardener support file ended unexpectedly: {path}"
                )
            chunks.append(chunk)
            remaining -= len(chunk)
        try:
            extra = os.read(descriptor, 1)
            after = os.fstat(descriptor)
            current = path.lstat()
            root_after = root.lstat()
        except OSError as exc:
            raise SystemExit(
                f"cannot recheck hardener support file: {path}"
            ) from exc
        if extra:
            raise SystemExit(f"hardener support file grew during read: {path}")
        if (
            not _same_file(opened, after)
            or not _same_file(after, current)
            or not _same_file(root_before, root_after)
        ):
            raise SystemExit(
                f"hardener support file changed during validation: {path}"
            )
    finally:
        os.close(descriptor)

    payload = b"".join(chunks)
    if _git_blob_digest(payload) != expected_blob:
        raise SystemExit(
            f"hardener support file does not match its reviewed blob: {path}"
        )
    return path


def _trusted_driver_at(root: pathlib.Path) -> pathlib.Path:
    return _trusted_regular(root / DRIVER_NAME, EXPECTED_DRIVER_BLOB)


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


def _resolve_implementation() -> pathlib.Path:
    entrypoint = pathlib.Path(__file__).absolute()
    try:
        return _trusted_regular(
            entrypoint.parent / IMPLEMENTATION_NAME,
            EXPECTED_IMPLEMENTATION_BLOB,
        )
    except (FileNotFoundError, OSError, SystemExit):
        pass
    explicit_root = os.environ.get("HP_HARDENER_SOURCE_ROOT", "")
    if not explicit_root:
        raise SystemExit("a unique trusted hardener implementation root is required")
    source_root = pathlib.Path(explicit_root)
    if not source_root.is_absolute():
        raise SystemExit("the hardener implementation root is not canonical")
    return _trusted_regular(
        source_root / "tools" / IMPLEMENTATION_NAME,
        EXPECTED_IMPLEMENTATION_BLOB,
    )


def _load_implementation(path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(
        "_hostpanel_hardener_entry_impl", path
    )
    if spec is None or spec.loader is None:
        raise SystemExit("could not load the reviewed hardener implementation")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


IMPLEMENTATION = _load_implementation(_resolve_implementation())
IMPLEMENTATION.CUSTOMBUILD_RUNTIME_BLOBS.update(RUNTIME_BLOB_OVERRIDES)
for _name in dir(IMPLEMENTATION):
    if not _name.startswith("__") and _name not in globals():
        globals()[_name] = getattr(IMPLEMENTATION, _name)

DRIVER = IMPLEMENTATION.DRIVER
DRIVER.EXPECTED_UPDATE_AGENT_BLOBS["tools/install-update-agent.sh"] = (
    EXPECTED_UPDATE_INSTALLER_BLOB
)
ORIGINAL_APPLY_POST_INSTALL_HEALTH_FIX = (
    IMPLEMENTATION.ORIGINAL_APPLY_POST_INSTALL_HEALTH_FIX
)
EXPECTED_UPDATE_AGENT_BLOBS = IMPLEMENTATION.EXPECTED_UPDATE_AGENT_BLOBS
CUSTOMBUILD_EXECUTABLES = IMPLEMENTATION.CUSTOMBUILD_EXECUTABLES
CUSTOMBUILD_MODULES = IMPLEMENTATION.CUSTOMBUILD_MODULES
CUSTOMBUILD_RUNTIME_MODES = IMPLEMENTATION.CUSTOMBUILD_RUNTIME_MODES
CUSTOMBUILD_RUNTIME_BLOBS = IMPLEMENTATION.CUSTOMBUILD_RUNTIME_BLOBS


def _read_support_file(
    reviewed_root: pathlib.Path, relative_name: str, expected_blob: str
) -> bytes:
    manifest = IMPLEMENTATION.CUSTOMBUILD_RUNTIME_BLOBS
    previous = manifest.get(relative_name)
    manifest[relative_name] = expected_blob
    try:
        return IMPLEMENTATION._read_reviewed_runtime_file(
            reviewed_root, relative_name
        )
    finally:
        if previous is None:
            manifest.pop(relative_name, None)
        else:
            manifest[relative_name] = previous


def _verify_all_overlay_inputs(
    reviewed_root: pathlib.Path,
) -> dict[str, bytes]:
    if set(CUSTOMBUILD_RUNTIME_MODES) != set(CUSTOMBUILD_RUNTIME_BLOBS):
        raise SystemExit("CustomBuild runtime manifest is incomplete")
    for relative_name in CUSTOMBUILD_RUNTIME_MODES:
        IMPLEMENTATION._read_reviewed_runtime_file(
            reviewed_root, relative_name
        )
    return {
        relative_name: _read_support_file(
            reviewed_root, relative_name, expected_blob
        )
        for relative_name, (expected_blob, _mode) in HARDENER_SUPPORT_FILES.items()
    }


def _preflight_overlay_targets(target_root: pathlib.Path) -> None:
    IMPLEMENTATION._trusted_directory(target_root)
    target_tools = target_root / "tools"
    if os.path.lexists(target_tools):
        IMPLEMENTATION._trusted_directory(target_tools)

    expected_uid = os.geteuid()
    relative_names = tuple(CUSTOMBUILD_RUNTIME_MODES) + tuple(HARDENER_SUPPORT_FILES)
    for relative_name in relative_names:
        relative = pathlib.PurePosixPath(relative_name)
        if (
            relative.is_absolute()
            or len(relative.parts) != 2
            or relative.parts[0] != "tools"
            or relative.parts[1] in {"", ".", ".."}
        ):
            raise SystemExit(f"invalid CustomBuild overlay target: {relative_name}")
        destination = target_root.joinpath(*relative.parts)
        if not os.path.lexists(destination):
            continue
        try:
            existing = destination.lstat()
        except OSError as exc:
            raise SystemExit(
                f"cannot inspect CustomBuild overlay target: {destination}"
            ) from exc
        if (
            not stat.S_ISREG(existing.st_mode)
            or stat.S_ISLNK(existing.st_mode)
            or existing.st_uid != expected_uid
            or existing.st_nlink != 1
            or stat.S_IMODE(existing.st_mode) & 0o022
        ):
            raise SystemExit(f"unsafe CustomBuild overlay target: {destination}")


def synchronize_custombuild_runtime(
    reviewed_root: pathlib.Path, target_root: pathlib.Path
) -> None:
    reviewed_root = reviewed_root.absolute()
    target_root = target_root.absolute()
    verified_support = _verify_all_overlay_inputs(reviewed_root)
    _preflight_overlay_targets(target_root)

    # The preserved implementation re-verifies runtime files at its own
    # publication boundary. The preflight above guarantees that every runtime
    # and support input and destination is trusted before it can create or
    # replace target files.
    IMPLEMENTATION.synchronize_custombuild_runtime(reviewed_root, target_root)
    if reviewed_root == target_root:
        return
    for relative_name, (_expected_blob, mode) in HARDENER_SUPPORT_FILES.items():
        destination = target_root.joinpath(
            *pathlib.PurePosixPath(relative_name).parts
        )
        IMPLEMENTATION._publish_runtime_file(
            destination, verified_support[relative_name], mode
        )


def main() -> None:
    reviewed_root = IMPLEMENTATION._reviewed_source_root()
    target_root = IMPLEMENTATION._installer_source_root()
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
