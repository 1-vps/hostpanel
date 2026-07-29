#!/usr/bin/env bash
# HostPanel hardened installer launcher.
#
# install.base.sh is the preserved, reviewed installer blob. This launcher
# verifies that exact blob, derives a hardened copy with fail-closed transforms,
# syntax-checks it, and only then runs it from the complete source tree.
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
BASE_INSTALLER="$SCRIPT_DIR/install.base.sh"
HARDENER="$SCRIPT_DIR/tools/harden_install.py"
EXPECTED_BASE_BLOB="17424f62d177706a096d1f600e5a702c9ce99498"
GENERATED_INSTALLER=""

cleanup(){
  [[ -z "$GENERATED_INSTALLER" ]] || rm -f -- "$GENERATED_INSTALLER"
}
trap cleanup EXIT HUP INT TERM

die(){ printf 'Error: %s\n' "$*" >&2; exit 1; }

command -v git >/dev/null 2>&1 || die "git is required to verify install.base.sh"
command -v python3 >/dev/null 2>&1 || die "python3 is required to derive the hardened installer"
[[ -f "$BASE_INSTALLER" && ! -L "$BASE_INSTALLER" ]] \
  || die "install.base.sh is missing or unsafe; use the pinned bootstrap from the complete reviewed source"
[[ -f "$HARDENER" && ! -L "$HARDENER" ]] \
  || die "tools/harden_install.py is missing or unsafe"
[[ -f "$SCRIPT_DIR/tools/harden_install_runtime.py" && ! -L "$SCRIPT_DIR/tools/harden_install_runtime.py" ]] \
  || die "tools/harden_install_runtime.py is missing or unsafe"

ACTUAL_BASE_BLOB="$(git hash-object "$BASE_INSTALLER")" \
  || die "Could not hash install.base.sh"
[[ "$ACTUAL_BASE_BLOB" == "$EXPECTED_BASE_BLOB" ]] \
  || die "install.base.sh does not match the reviewed base blob"

GENERATED_INSTALLER="$(mktemp "$SCRIPT_DIR/.install.hardened.XXXXXX")" \
  || die "Could not allocate the hardened installer"
chmod 700 "$GENERATED_INSTALLER"
python3 "$HARDENER" "$BASE_INSTALLER" "$GENERATED_INSTALLER" \
  || die "Could not derive the hardened installer"
python3 - "$GENERATED_INSTALLER" <<'PYPRIVMANIFEST' \
  || die "Could not apply the reviewed privileged manifest parser"
import os
import pathlib
import stat
import sys

path = pathlib.Path(sys.argv[1])
if not path.is_file() or path.is_symlink():
    raise SystemExit(f"unsafe generated installer target: {path}")
old = '''while IFS= read -r worker; do
  [[ -z "$worker" || "$worker" == \\#* ]] && continue
  [[ -f "$PANEL_DIR/app/$worker" ]] || die "privileged file listed but missing: $worker"
  chmod 755 "$PANEL_DIR/app/$worker"
done <"$PANEL_DIR/app/privileged-files.txt"'''
new = '''while IFS= read -r manifest_line || [[ -n "$manifest_line" ]]; do
  [[ "$manifest_line" =~ ^[[:space:]]*$ || "$manifest_line" =~ ^[[:space:]]*# ]] && continue
  read -r manifest_path manifest_owner manifest_mode manifest_extra <<<"$manifest_line"
  [[ -n "$manifest_path" && -z "${manifest_extra:-}" ]] \\
    || die "malformed privileged file manifest entry"
  [[ "$manifest_path" != /* && "$manifest_path" != *..* && "$manifest_path" != *//* ]] \\
    || die "unsafe privileged file manifest path: $manifest_path"
  if [[ "$manifest_path" == */* ]]; then
    [[ "$manifest_path" == app/* && "$manifest_path" != app/ ]] \\
      || die "privileged manifest paths with directories must be app-relative: $manifest_path"
    manifest_target="$PANEL_DIR/$manifest_path"
  else
    manifest_target="$PANEL_DIR/app/$manifest_path"
  fi
  if [[ -n "${manifest_owner:-}" || -n "${manifest_mode:-}" ]]; then
    [[ -n "${manifest_owner:-}" && -n "${manifest_mode:-}" ]] \\
      || die "privileged manifest owner and mode must be specified together: $manifest_path"
  else
    manifest_owner=root:root
    manifest_mode=0755
  fi
  [[ "$manifest_owner" =~ ^[A-Za-z_][A-Za-z0-9_-]*:[A-Za-z_][A-Za-z0-9_-]*$ ]] \\
    || die "invalid privileged manifest owner: $manifest_owner"
  [[ "$manifest_mode" =~ ^0?[0-7]{3}$ ]] \\
    || die "invalid privileged manifest mode: $manifest_mode"
  [[ -f "$manifest_target" && ! -L "$manifest_target" ]] \\
    || die "privileged file listed but missing or unsafe: $manifest_path"
  chown -- "$manifest_owner" "$manifest_target" \\
    || die "could not set privileged file ownership: $manifest_path"
  chmod -- "$manifest_mode" "$manifest_target" \\
    || die "could not set privileged file mode: $manifest_path"
done <"$PANEL_DIR/app/privileged-files.txt"'''
text = path.read_text(encoding="utf-8")
old_count = text.count(old)
new_count = text.count(new)
if old_count == 1 and new_count == 0:
    updated = text.replace(old, new, 1)
elif old_count == 0 and new_count == 1:
    updated = text
else:
    raise SystemExit(
        f"unexpected privileged manifest parser shape: old={old_count} new={new_count}"
    )
mode = stat.S_IMODE(path.stat().st_mode)
temporary = path.with_name(f".{path.name}.privileged-manifest.{os.getpid()}")
try:
    temporary.write_text(updated, encoding="utf-8")
    os.chmod(temporary, mode)
    os.replace(temporary, path)
finally:
    temporary.unlink(missing_ok=True)
PYPRIVMANIFEST
bash -n "$GENERATED_INSTALLER" \
  || die "The derived installer failed Bash syntax validation"

set +e
bash "$GENERATED_INSTALLER" "$@"
status=$?
set -e
exit "$status"
