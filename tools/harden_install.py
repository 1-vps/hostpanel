#!/usr/bin/env python3
"""Verify and run the reviewed HostPanel installer hardener driver."""
from __future__ import annotations

import importlib.util
import os
import pathlib
import stat
import subprocess
import sys

DRIVER_NAME = "harden_install_driver.py"
EXPECTED_DRIVER_BLOB = "19d29feb55969d6925c87ea5b8419a624d4cdb52"
EXPECTED_UPDATE_INSTALLER_BLOB = "8b191c233fdff7f62c8b8ddf4a1077451e2961c1"


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


def trusted_driver() -> pathlib.Path:
    entrypoint = pathlib.Path(__file__).absolute()
    root = entrypoint.parent
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
    if (
        (before.st_dev, before.st_ino, before.st_mode, before.st_uid,
         before.st_gid, before.st_nlink, before.st_size, before.st_mtime_ns)
        !=
        (after.st_dev, after.st_ino, after.st_mode, after.st_uid,
         after.st_gid, after.st_nlink, after.st_size, after.st_mtime_ns)
        or
        (root_before.st_dev, root_before.st_ino, root_before.st_mode,
         root_before.st_uid, root_before.st_gid, root_before.st_nlink,
         root_before.st_size, root_before.st_mtime_ns)
        !=
        (root_after.st_dev, root_after.st_ino, root_after.st_mode,
         root_after.st_uid, root_after.st_gid, root_after.st_nlink,
         root_after.st_size, root_after.st_mtime_ns)
    ):
        raise SystemExit("the hardener driver changed during validation")
    return driver


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

for _name in dir(DRIVER):
    if not _name.startswith("__") and _name not in globals():
        globals()[_name] = getattr(DRIVER, _name)

EXPECTED_UPDATE_AGENT_BLOBS = DRIVER.EXPECTED_UPDATE_AGENT_BLOBS


def main() -> None:
    DRIVER.EXPECTED_UPDATE_AGENT_BLOBS["tools/install-update-agent.sh"] = (
        EXPECTED_UPDATE_INSTALLER_BLOB
    )
    DRIVER.main()


if __name__ == "__main__":
    main()
