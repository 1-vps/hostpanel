#!/usr/bin/env bash
set -euo pipefail

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
unset ENV PYTHONPATH PYTHONHOME LD_PRELOAD LD_LIBRARY_PATH

root="$(git rev-parse --show-toplevel)"
cd "$root"

die() {
  printf 'CircleCI QEMU acceptance failed: %s\n' "$*" >&2
  exit 1
}

qemu_token=""
cleanup_secret() {
  unset qemu_token HP_QEMU_REPO_TOKEN
}
trap cleanup_secret EXIT

.circleci/scripts/verify-build.sh
[[ "${HP_CIRCLECI_EVENT:-}" == "push" ]] || die "QEMU requires a push event"
[[ "${HP_CIRCLECI_BRANCH:-}" == "main" ]] || die "QEMU is restricted to main"
[[ "${HP_CIRCLECI_DEFAULT_BRANCH:-}" == "true" ]] || die "main is not the default branch"
[[ "${HP_CIRCLECI_PUSH_SHA:-}" == "$CIRCLE_SHA1" ]] || die "push SHA differs from checkout"

command -v qemu-system-x86_64 >/dev/null 2>&1 || die "qemu-system-x86_64 is unavailable"
command -v setfacl >/dev/null 2>&1 || die "setfacl is unavailable"
[[ -c /dev/kvm && -r /dev/kvm && -w /dev/kvm ]] || die "/dev/kvm is unavailable"

token_file="${HP_CIRCLECI_QEMU_TOKEN_FILE:-/run/hostpanel-circleci/qemu-repo-token}"
[[ "$token_file" == "/run/hostpanel-circleci/qemu-repo-token" ]] || die "unexpected token path"
[[ -f "$token_file" && ! -L "$token_file" ]] || die "worker-local QEMU token is unavailable"
[[ "$(stat -c '%h' -- "$token_file")" == "1" ]] || die "QEMU token has multiple links"
[[ "$(stat -c '%u' -- "$token_file")" == "$(id -u)" ]] || die "QEMU token owner is unsafe"
mode="$(stat -c '%a' -- "$token_file")"
[[ "$mode" == "400" || "$mode" == "600" ]] || die "QEMU token mode is unsafe"
token_size="$(stat -c '%s' -- "$token_file")"
qemu_token="$(cat -- "$token_file")"
rm -f -- "$token_file"
[[ "${#qemu_token}" == "$token_size" ]] || die "QEMU token contains trailing bytes"
[[ ${#qemu_token} -ge 20 && ${#qemu_token} -le 512 ]] || die "QEMU token length is unsafe"
[[ "$qemu_token" != *$'\n'* && "$qemu_token" != *$'\r'* ]] || die "QEMU token contains a line break"

HP_CIRCLECI_CHECK=qemu-contracts .circleci/scripts/run-check.sh

artifact_dir="artifacts/qemu-vm-acceptance"
sealed_archive="artifacts/qemu-vm-acceptance.tar"
[[ ! -e "$artifact_dir" && ! -L "$artifact_dir" ]] || die "evidence directory already exists"
[[ ! -e "$sealed_archive" && ! -L "$sealed_archive" ]] || die "sealed evidence already exists"
mkdir -p "$artifact_dir"

run_status=0
HP_QEMU_REPO_TOKEN="$qemu_token" \
HP_QEMU_REVIEWED_COMMIT_SHA="$CIRCLE_SHA1" \
HP_QEMU_MTA="postfix" \
  bash tools/run-qemu-vm-acceptance.sh || run_status=$?
unset qemu_token HP_QEMU_REPO_TOKEN

python3 tools/prepare-qemu-evidence.py
python3 tools/sanitize-qemu-evidence.py "$artifact_dir"
python3 tools/seal-qemu-evidence.py "$artifact_dir" "$sealed_archive"
[[ -f "$sealed_archive" && ! -L "$sealed_archive" ]] || die "sealed evidence is invalid"
[[ "$(stat -c '%h' -- "$sealed_archive")" == "1" ]] || die "sealed evidence has multiple links"
archive_size="$(stat -c '%s' -- "$sealed_archive")"
[[ "$archive_size" =~ ^[0-9]+$ ]] || die "sealed evidence size is invalid"
((archive_size > 0 && archive_size <= 1073741824)) || die "sealed evidence size is outside bounds"
sha256sum "$sealed_archive"

# The trusted main pipeline uploads only this sanitized, sealed archive. No raw
# evidence directory, token, cache, workspace or PR artifact is retained.
exit "$run_status"
