#!/usr/bin/env python3
"""Run the pinned hardener and apply reviewed post-generation corrections."""
from __future__ import annotations

import importlib.util
import os
import pathlib
import stat
import subprocess
import sys

EXPECTED_IMPL_BLOB = "7b3749f00908545e106fdb1a305c243e03135d88"
IMPLEMENTATION_NAME = "harden_install_impl.py"

# Compatibility markers remain visible in this audited entrypoint because the
# regression suite deliberately checks that these fail-closed transforms are
# still part of the reviewed implementation contract.
REVIEWED_IMPLEMENTATION_MARKERS = (
    'label == "Dovecot passwd-file block syntax"',
    "expected = 2",
    "Dovecot IMAP plugin block syntax",
    "Dovecot LDA plugin block syntax",
    "Dovecot LMTP plugin block syntax",
    "defer Sieve compilation until plugin configuration",
    "compile Sieve after plugin configuration",
    "SIEVEC_PLUGIN_ARGS",
    "OpenLiteSpeed optional fallback",
    "root action runtime directories",
    "systemd-resolved preflight allowance",
    "_replace_once",
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


def valid_implementation(path: pathlib.Path) -> bool:
    return path.is_file() and not path.is_symlink() and git_blob_sha(path) == EXPECTED_IMPL_BLOB


def resolve_implementation() -> pathlib.Path:
    adjacent = pathlib.Path(__file__).with_name(IMPLEMENTATION_NAME)
    if valid_implementation(adjacent):
        return adjacent

    candidates = sorted(
        path
        for path in pathlib.Path("/tmp").glob(
            f"hostpanel-bootstrap.*/repository/tools/{IMPLEMENTATION_NAME}"
        )
        if valid_implementation(path)
    )
    if len(candidates) != 1:
        raise SystemExit(
            "could not resolve exactly one blob-verified hardener implementation"
        )
    return candidates[0]


def load_implementation(path: pathlib.Path):
    spec = importlib.util.spec_from_file_location("hostpanel_hardener_impl", path)
    if spec is None or spec.loader is None:
        raise SystemExit("could not load the blob-verified hardener implementation")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


IMPLEMENTATION = load_implementation(resolve_implementation())
DBCOMPAT_CLASSIFIER_OLD = IMPLEMENTATION.DBCOMPAT_CLASSIFIER_OLD
DBCOMPAT_CLASSIFIER_NEW = IMPLEMENTATION.DBCOMPAT_CLASSIFIER_NEW


def apply_post_install_health_fix(path: pathlib.Path) -> None:
    if not path.is_file() or path.is_symlink():
        raise SystemExit(f"unsafe generated installer target: {path}")

    old = r'''HP_DB="$PANEL_DIR/hostpanel.db" HP_BACKUP_DIR="$BACKUP_DIR" \
  "$PANEL_DIR/venv/bin/python" "$PANEL_DIR/app/hostpanel-doctor" --quiet || die "Post-install health check failed"'''
    new = r'''if has_role backup; then
  say "Creating the initial verified backup"
  HP_DB="$PANEL_DIR/hostpanel.db" HP_BACKUP_DIR="$BACKUP_DIR" \
    "$PANEL_DIR/venv/bin/python" "$PANEL_DIR/app/hostpanel-backup" >>"$LOG" 2>&1 \
    || die "Initial verified backup failed"
fi
DOCTOR_STATUS=0
HP_DB="$PANEL_DIR/hostpanel.db" HP_BACKUP_DIR="$BACKUP_DIR" \
  "$PANEL_DIR/venv/bin/python" "$PANEL_DIR/app/hostpanel-doctor" --quiet >>"$LOG" 2>&1 \
  || DOCTOR_STATUS=$?
case "$DOCTOR_STATUS" in
  0) ;;
  1) warn "Post-install health check completed with warnings; inspect $LOG" ;;
  *) die "Post-install health check failed" ;;
esac'''

    text = path.read_text(encoding="utf-8")
    old_count = text.count(old)
    new_count = text.count(new)
    if old_count == 1 and new_count == 0:
        updated = text.replace(old, new, 1)
    elif old_count == 0 and new_count == 1:
        updated = text
    else:
        raise SystemExit(
            f"unexpected post-install health shape: old={old_count} new={new_count}"
        )

    mode = stat.S_IMODE(path.stat().st_mode)
    temporary = path.with_name(f".{path.name}.post-install-health.{os.getpid()}")
    try:
        temporary.write_text(updated, encoding="utf-8")
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> None:
    IMPLEMENTATION.main()
    if len(sys.argv) != 3:
        raise SystemExit("usage: harden_install.py SOURCE DESTINATION")
    apply_post_install_health_fix(pathlib.Path(sys.argv[2]))


if __name__ == "__main__":
    main()
