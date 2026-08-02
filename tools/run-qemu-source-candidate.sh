#!/usr/bin/env bash
# Run the existing QEMU acceptance harness against the exact reproduced source
# candidate without changing the production bootstrap committed to the branch.
set -Eeuo pipefail

REPO_ROOT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
PRODUCTION_BOOTSTRAP="$REPO_ROOT/bootstrap-install.sh"
CANDIDATE_BOOTSTRAP="$REPO_ROOT/tools/bootstrap-source-candidate.sh"
HARNESS="$REPO_ROOT/tools/run-qemu-vm-acceptance.sh"
BACKUP=""
ORIGINAL_SHA=""

fail(){ printf 'Error: %s\n' "$*" >&2; exit 1; }
restore_bootstrap(){
  local status=$?
  trap - EXIT HUP INT TERM
  if [[ -n "$BACKUP" && -f "$BACKUP" ]]; then
    install -m 0755 "$BACKUP" "$PRODUCTION_BOOTSTRAP"
    rm -f -- "$BACKUP"
    BACKUP=""
  fi
  if [[ -n "$ORIGINAL_SHA" && -f "$PRODUCTION_BOOTSTRAP" ]]; then
    [[ "$(sha256sum "$PRODUCTION_BOOTSTRAP" | awk '{print $1}')" == "$ORIGINAL_SHA" ]] \
      || { printf 'Error: production bootstrap restoration failed\n' >&2; exit 1; }
  fi
  exit "$status"
}
trap restore_bootstrap EXIT HUP INT TERM

for path in "$PRODUCTION_BOOTSTRAP" "$CANDIDATE_BOOTSTRAP" "$HARNESS" "$REPO_ROOT/SOURCE_VERSION"; do
  [[ -f "$path" && ! -L "$path" ]] || fail "unsafe or missing QEMU candidate input: $path"
done

SOURCE_VERSION_RAW="$(cat "$REPO_ROOT/SOURCE_VERSION")"
SOURCE_VERSION="${SOURCE_VERSION_RAW%$'\n'}"
[[ "$SOURCE_VERSION_RAW" == "$SOURCE_VERSION" || "$SOURCE_VERSION_RAW" == "$SOURCE_VERSION"$'\n' ]] \
  || fail "SOURCE_VERSION must contain exactly one canonical line"
[[ "$SOURCE_VERSION" =~ ^[0-9]+(\.[0-9]+){2}([+-][0-9A-Za-z][0-9A-Za-z.-]*)?$ ]] \
  || fail "SOURCE_VERSION is not semantic"

ORIGINAL_SHA="$(sha256sum "$PRODUCTION_BOOTSTRAP" | awk '{print $1}')"
BACKUP="$(mktemp "$REPO_ROOT/.bootstrap-install.qemu-backup.XXXXXX")" \
  || fail "could not create private bootstrap backup"
chmod 600 "$BACKUP"
cat "$PRODUCTION_BOOTSTRAP" >"$BACKUP"
install -m 0755 "$CANDIDATE_BOOTSTRAP" "$PRODUCTION_BOOTSTRAP"
[[ "$(sha256sum "$PRODUCTION_BOOTSTRAP" | awk '{print $1}')" == "$(sha256sum "$CANDIDATE_BOOTSTRAP" | awk '{print $1}')" ]] \
  || fail "candidate bootstrap promotion failed"

export HP_QEMU_EXPECTED_VERSION="$SOURCE_VERSION"
bash "$HARNESS"
