#!/usr/bin/env bash
set -euo pipefail

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
unset BASH_ENV ENV PYTHONPATH PYTHONHOME LD_PRELOAD LD_LIBRARY_PATH

root="$(git rev-parse --show-toplevel)"
cd "$root"

die() {
  printf 'Buildkite QEMU acceptance failed: %s\n' "$*" >&2
  exit 1
}

qemu_token=""
cleanup_secret() {
  unset qemu_token HP_QEMU_REPO_TOKEN
}
trap cleanup_secret EXIT

.buildkite/scripts/verify-build.sh

[[ "${BUILDKITE_SOURCE:-}" == "webhook" ]] || {
  die "privileged QEMU acceptance requires a webhook-triggered build"
}
[[ "${BUILDKITE_PULL_REQUEST:-false}" == "false" ]] || {
  die "privileged QEMU acceptance is forbidden for pull-request builds"
}
[[ -z "${BUILDKITE_PULL_REQUEST_REPO:-}" ]] || {
  die "non-PR QEMU acceptance unexpectedly contains a pull-request repository"
}
[[ "${BUILDKITE_BRANCH:-}" == "main" ]] || {
  die "privileged QEMU acceptance is restricted to main"
}

command -v buildkite-agent >/dev/null 2>&1 || die "buildkite-agent is unavailable"
command -v qemu-system-x86_64 >/dev/null 2>&1 || die "qemu-system-x86_64 is unavailable"
command -v setfacl >/dev/null 2>&1 || die "setfacl is unavailable"
[[ -c /dev/kvm && -r /dev/kvm && -w /dev/kvm ]] || {
  die "/dev/kvm is unavailable to the Buildkite agent user"
}

# Run all non-secret QEMU contracts before requesting the privileged token.
HP_BUILDKITE_CHECK=qemu-contracts .buildkite/scripts/run-check.sh

qemu_token="$(buildkite-agent secret get HP_QEMU_REPO_TOKEN)"
[[ ${#qemu_token} -ge 20 && ${#qemu_token} -le 512 ]] || {
  die "HP_QEMU_REPO_TOKEN has an unsafe length"
}
[[ "$qemu_token" != *$'\n'* && "$qemu_token" != *$'\r'* ]] || {
  die "HP_QEMU_REPO_TOKEN contains a line break"
}

artifact_dir="artifacts/qemu-vm-acceptance"
sealed_archive="artifacts/qemu-vm-acceptance.tar"
[[ ! -e "$artifact_dir" && ! -L "$artifact_dir" ]] || die "QEMU evidence directory already exists"
[[ ! -e "$sealed_archive" && ! -L "$sealed_archive" ]] || die "sealed QEMU evidence already exists"
mkdir -p "$artifact_dir"

run_status=0
HP_QEMU_REPO_TOKEN="$qemu_token" \
HP_QEMU_REVIEWED_COMMIT_SHA="$BUILDKITE_COMMIT" \
HP_QEMU_MTA="postfix" \
  bash tools/run-qemu-vm-acceptance.sh || run_status=$?
unset qemu_token HP_QEMU_REPO_TOKEN

sanitize_status=0
python3 tools/prepare-qemu-evidence.py || sanitize_status=$?
if ((sanitize_status == 0)); then
  python3 tools/sanitize-qemu-evidence.py "$artifact_dir" || sanitize_status=$?
fi
if ((sanitize_status == 0)); then
  python3 tools/seal-qemu-evidence.py "$artifact_dir" "$sealed_archive" || sanitize_status=$?
fi

if ((sanitize_status != 0)); then
  die "evidence sanitization or sealing failed"
fi

[[ -f "$sealed_archive" && ! -L "$sealed_archive" ]] || die "sealed evidence is not a regular file"
[[ "$(stat -c '%h' -- "$sealed_archive")" == "1" ]] || die "sealed evidence has multiple links"
archive_size="$(stat -c '%s' -- "$sealed_archive")"
[[ "$archive_size" =~ ^[0-9]+$ ]] || die "sealed evidence size is invalid"
((archive_size > 0 && archive_size <= 1073741824)) || die "sealed evidence size is outside bounds"

buildkite-agent artifact upload --upload-skip-symlinks --literal "$sealed_archive"

exit "$run_status"
