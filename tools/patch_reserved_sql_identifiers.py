#!/usr/bin/env python3
"""Inject the reviewed reserved-SQL-identifier runtime patch into an installer."""
from __future__ import annotations

import pathlib
import sys


MARKER = r'''python3 -m py_compile "$PANEL_DIR/app/dbcompat.py" >>"$LOG" 2>&1 \
  || die "Patched PostgreSQL compatibility module does not compile"
sync_optional_tree "$SOURCE_ROOT/releases" "$PANEL_DIR/releases"'''

RUNTIME_PATCH = r'''python3 -m py_compile "$PANEL_DIR/app/dbcompat.py" >>"$LOG" 2>&1 \
  || die "Patched PostgreSQL compatibility module does not compile"
python3 - "$PANEL_DIR/app/store.py" "$PANEL_DIR/app/platform_store.py" <<'PYRESERVEDSQL' >>"$LOG" 2>&1 \
  || die "Could not apply the reviewed reserved SQL identifier patch"
import os
import pathlib
import stat
import sys

patches = {
    pathlib.Path(sys.argv[1]): (
        (
            "traffic cursor schema",
            "    offset       INTEGER NOT NULL DEFAULT 0,",
            "    cursor_offset INTEGER NOT NULL DEFAULT 0,",
            1,
        ),
    ),
    pathlib.Path(sys.argv[2]): (
        (
            "platform log cursor schema",
            "    offset      INTEGER NOT NULL DEFAULT 0,",
            "    cursor_offset INTEGER NOT NULL DEFAULT 0,",
            1,
        ),
        (
            "platform cursor select",
            "SELECT inode,offset FROM platform_log_cursors WHERE source=?",
            "SELECT inode,cursor_offset FROM platform_log_cursors WHERE source=?",
            1,
        ),
        (
            "platform cursor row access",
            'cursor["offset"]',
            'cursor["cursor_offset"]',
            2,
        ),
        (
            "platform cursor insert",
            "INSERT INTO platform_log_cursors(source,inode,offset,updated) VALUES(?,?,?,?) ",
            "INSERT INTO platform_log_cursors(source,inode,cursor_offset,updated) VALUES(?,?,?,?) ",
            1,
        ),
        (
            "platform cursor upsert",
            "ON CONFLICT(source) DO UPDATE SET inode=excluded.inode,offset=excluded.offset,updated=excluded.updated",
            "ON CONFLICT(source) DO UPDATE SET inode=excluded.inode,cursor_offset=excluded.cursor_offset,updated=excluded.updated",
            1,
        ),
    ),
}

for path, replacements in patches.items():
    if not path.is_file() or path.is_symlink():
        raise SystemExit(f"unsafe runtime patch target: {path}")
    text = path.read_text(encoding="utf-8")
    updated = text
    for label, old, new, expected in replacements:
        old_count = updated.count(old)
        new_count = updated.count(new)
        if old_count == expected and new_count == 0:
            updated = updated.replace(old, new, expected)
        elif old_count == 0 and new_count == expected:
            continue
        else:
            raise SystemExit(
                f"unexpected {label} shape in {path.name}: old={old_count} new={new_count}"
            )
    if updated == text:
        continue
    mode = stat.S_IMODE(path.stat().st_mode)
    temporary = path.with_name(f".{path.name}.hostpanel.{os.getpid()}")
    try:
        temporary.write_text(updated, encoding="utf-8")
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
PYRESERVEDSQL
python3 -m py_compile "$PANEL_DIR/app/store.py" "$PANEL_DIR/app/platform_store.py" >>"$LOG" 2>&1 \
  || die "Patched SQL cursor modules do not compile"
sync_optional_tree "$SOURCE_ROOT/releases" "$PANEL_DIR/releases"'''


def patch_installer(text: str) -> str:
    marker_count = text.count(MARKER)
    patch_count = text.count(RUNTIME_PATCH)
    if marker_count == 1 and patch_count == 0:
        return text.replace(MARKER, RUNTIME_PATCH, 1)
    if marker_count == 0 and patch_count == 1:
        return text
    raise SystemExit(
        f"unexpected installer reserved-SQL patch shape: marker={marker_count} patch={patch_count}"
    )


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: patch_reserved_sql_identifiers.py SOURCE DESTINATION")
    source = pathlib.Path(sys.argv[1])
    destination = pathlib.Path(sys.argv[2])
    if not source.is_file() or source.is_symlink():
        raise SystemExit(f"unsafe installer source: {source}")
    destination.write_text(patch_installer(source.read_text(encoding="utf-8")), encoding="utf-8")
    destination.chmod(0o700)


if __name__ == "__main__":
    main()
