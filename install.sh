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
bash -n "$GENERATED_INSTALLER" \
  || die "The derived installer failed Bash syntax validation"

set +e
bash "$GENERATED_INSTALLER" "$@"
status=$?
set -e
exit "$status"
