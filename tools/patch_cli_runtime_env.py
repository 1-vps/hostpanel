#!/usr/bin/env python3
"""Install strict runtime loaders and reviewed CLI compatibility corrections."""
from __future__ import annotations

import os
import pathlib
import stat
import sys


LOADER = '''_RUNTIME_ENV_KEY = _runtime_re.compile(r"HP_[A-Z0-9_]{1,128}")


def _load_runtime_environment() -> None:
    config = Path(__file__).resolve().parent.parent / "config.env"
    try:
        metadata = config.stat(follow_symlinks=False)
    except OSError as exc:
        if _runtime_os.environ.get("HP_TEST_MODE") == "1":
            return
        raise RuntimeError("HostPanel runtime environment is unavailable") from exc
    if (not _runtime_stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_mode & 0o022):
        raise RuntimeError("HostPanel runtime environment is unsafe")
    seen = set()
    for raw in config.read_text(encoding="utf-8").splitlines():
        if not raw or raw.lstrip().startswith("#"):
            continue
        key, separator, value = raw.partition("=")
        if (not separator or not _RUNTIME_ENV_KEY.fullmatch(key)
                or key in seen or "\\x00" in value):
            raise RuntimeError("HostPanel runtime environment is malformed")
        seen.add(key)
        _runtime_os.environ.setdefault(key, value)
    required = ("HP_SECRET", "HP_DATABASE_URL_FILE", "HP_MASTER_KEY_FILE")
    if (_runtime_os.environ.get("HP_TEST_MODE") != "1"
            and any(not _runtime_os.environ.get(key) for key in required)):
        raise RuntimeError("HostPanel runtime environment is incomplete")
'''

BACKUP_OLD = '''sys.path.insert(0, str(Path(__file__).resolve().parent))

from modules import backups  # noqa: E402'''
BACKUP_NEW = '''sys.path.insert(0, str(Path(__file__).resolve().parent))

import os as _runtime_os
import re as _runtime_re
import stat as _runtime_stat

''' + LOADER + '''
_load_runtime_environment()

from modules import backups  # noqa: E402'''

DOCTOR_OLD = '''sys.path.insert(0, str(Path(__file__).resolve().parent))

import osrelease'''
DOCTOR_NEW = '''sys.path.insert(0, str(Path(__file__).resolve().parent))

import os as _runtime_os
import re as _runtime_re
import stat as _runtime_stat

''' + LOADER + '''
_load_runtime_environment()

import osrelease'''

MYSQL_ADMIN_OLD = '''    prelude = "SET SESSION local_infile=0;\\nSET SESSION sql_mode='NO_BACKSLASH_ESCAPES';\\n"'''
MYSQL_ADMIN_NEW = '''    prelude = "SET SESSION sql_mode='NO_BACKSLASH_ESCAPES';\\n"'''


def patch(target: pathlib.Path, old: str, new: str, label: str) -> None:
    if not target.is_file() or target.is_symlink():
        raise SystemExit(f"unsafe {label} target: {target}")
    text = target.read_text(encoding="utf-8")
    old_count = text.count(old)
    new_count = text.count(new)
    if old_count == 1 and new_count == 0:
        updated = text.replace(old, new, 1)
    elif old_count == 0 and new_count == 1:
        updated = text
    else:
        raise SystemExit(
            f"unexpected {label} runtime shape: old={old_count} new={new_count}"
        )
    if updated == text:
        return
    mode = stat.S_IMODE(target.stat().st_mode)
    temporary = target.with_name(f".{target.name}.runtime-env.{os.getpid()}")
    try:
        temporary.write_text(updated, encoding="utf-8")
        os.chmod(temporary, mode)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> None:
    if len(sys.argv) != 4:
        raise SystemExit(
            "usage: patch_cli_runtime_env.py HOSTPANEL_BACKUP HOSTPANEL_DOCTOR HOSTPANEL_MYSQL_ADMIN"
        )
    patch(pathlib.Path(sys.argv[1]), BACKUP_OLD, BACKUP_NEW, "backup CLI")
    patch(pathlib.Path(sys.argv[2]), DOCTOR_OLD, DOCTOR_NEW, "doctor CLI")
    patch(
        pathlib.Path(sys.argv[3]),
        MYSQL_ADMIN_OLD,
        MYSQL_ADMIN_NEW,
        "MariaDB root gateway",
    )


if __name__ == "__main__":
    main()
