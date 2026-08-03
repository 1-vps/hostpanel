#!/usr/bin/env python3
"""Run the reviewed HostPanel hardener, then apply CustomBuild integration."""
from __future__ import annotations

import importlib.util
import os
import pathlib
import stat
import subprocess
import sys

EXPECTED_CORE_BLOB = "cca478ddcdc802a7c9ea0b0429b159b88972c6b9"


def git_blob(path: pathlib.Path) -> str:
    completed = subprocess.run(
        ["git", "hash-object", "--no-filters", str(path)],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else ""


def load_core():
    path = pathlib.Path(__file__).with_name("harden_install_core.py")
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or git_blob(path) != EXPECTED_CORE_BLOB
    ):
        raise SystemExit("the reviewed HostPanel hardener core is missing or unsafe")
    spec = importlib.util.spec_from_file_location("hostpanel_hardener_core", path)
    if spec is None or spec.loader is None:
        raise SystemExit("could not load the reviewed HostPanel hardener core")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def replace_once(text: str, old: str, new: str, label: str) -> str:
    old_count = text.count(old)
    new_count = text.count(new)
    if old_count == 1 and new_count == 0:
        return text.replace(old, new, 1)
    if old_count == 0 and new_count == 1:
        return text
    raise SystemExit(f"unexpected {label} shape: old={old_count} new={new_count}")


def apply_custombuild(path: pathlib.Path) -> None:
    if not path.is_file() or path.is_symlink():
        raise SystemExit(f"unsafe generated installer target: {path}")

    shapes = (
        (
            'RSPAMD_REPO_MODE="${HP_RSPAMD_REPO:-off}"',
            'RSPAMD_REPO_MODE="${HP_RSPAMD_REPO:-off}"\n'
            'WEBSERVER_MODE="${HP_WEBSERVER_MODE:-nginx_apache}"',
            "CustomBuild webserver variable",
        ),
        (
            '[[ "$MAIL_MTA" =~ ^(postfix|exim)$ ]] || die "Unsupported MTA: $MAIL_MTA (choose postfix or exim)"',
            '''[[ "$MAIL_MTA" =~ ^(postfix|exim)$ ]] || die "Unsupported MTA: $MAIL_MTA (choose postfix or exim)"
if [[ "$REINSTALL" == yes && -z "${HP_WEBSERVER_MODE+x}" \
     && -r /etc/hostpanel/webserver-mode ]]; then
  previous_webserver_mode="$(tr -d '[:space:]' </etc/hostpanel/webserver-mode)"
  [[ "$previous_webserver_mode" =~ ^(nginx_apache|nginx|apache|openlitespeed)$ ]] \
    && WEBSERVER_MODE="$previous_webserver_mode"
fi
[[ "$WEBSERVER_MODE" =~ ^(nginx_apache|nginx|apache|openlitespeed)$ ]] \
  || die "Unsupported webserver mode: $WEBSERVER_MODE"''',
            "CustomBuild webserver validation",
        ),
        (
            '''if has_role web && pkg_available "$(pkg_name openlitespeed)"; then
say "Installing OpenLiteSpeed and LSPHP"''',
            '''if has_role web && [[ "$WEBSERVER_MODE" == openlitespeed ]] \
   && pkg_available "$(pkg_name openlitespeed)"; then
say "Installing OpenLiteSpeed and LSPHP"''',
            "optional OpenLiteSpeed installation",
        ),
        (
            '''if has_role web; then
say "Configuring OpenLiteSpeed as a private per-domain backend"''',
            '''if has_role web && [[ "$WEBSERVER_MODE" == openlitespeed ]]; then
say "Configuring OpenLiteSpeed as a private per-domain backend"''',
            "optional OpenLiteSpeed configuration",
        ),
        (
            '  warn "OpenLiteSpeed backend unavailable; nginx and Apache remain active"',
            '''  [[ "$WEBSERVER_MODE" != openlitespeed ]] \
    || die "OpenLiteSpeed mode was requested but the runtime is unavailable; use hostpanel-build after configuring a supported package source"
  warn "OpenLiteSpeed backend unavailable; nginx and Apache remain active"''',
            "requested OpenLiteSpeed availability",
        ),
        (
            'sync_optional_tree "$SOURCE_ROOT/tools" "$PANEL_DIR/tools"',
            '''sync_optional_tree "$SOURCE_ROOT/tools" "$PANEL_DIR/tools"
CUSTOMBUILD_INSTALLER="$SOURCE_ROOT/tools/install-hostpanel-build.sh"
[[ -f "$CUSTOMBUILD_INSTALLER" && ! -L "$CUSTOMBUILD_INSTALLER" ]] \
  || die "The reviewed HostPanel CustomBuild installer is missing or unsafe"
HP_WEBSERVER_MODE="$WEBSERVER_MODE" bash "$CUSTOMBUILD_INSTALLER" >>"$LOG" 2>&1 \
  || die "Could not install the HostPanel CustomBuild maintenance tool"''',
            "CustomBuild installation",
        ),
    )

    updated = path.read_text(encoding="utf-8")
    for old, new, label in shapes:
        updated = replace_once(updated, old, new, label)

    mode = stat.S_IMODE(path.stat().st_mode)
    temporary = path.with_name(f".{path.name}.custombuild.{os.getpid()}")
    try:
        temporary.write_text(updated, encoding="utf-8")
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> None:
    core = load_core()
    core.main()
    if len(sys.argv) != 3:
        raise SystemExit("usage: harden_install.py SOURCE DESTINATION")
    apply_custombuild(pathlib.Path(sys.argv[2]))


if __name__ == "__main__":
    main()
