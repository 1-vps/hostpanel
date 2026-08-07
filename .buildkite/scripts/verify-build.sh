#!/usr/bin/env bash
set -euo pipefail

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
unset BASH_ENV ENV PYTHONPATH PYTHONHOME LD_PRELOAD LD_LIBRARY_PATH

die() {
  printf 'Buildkite checkout verification failed: %s\n' "$*" >&2
  exit 1
}

command -v git >/dev/null 2>&1 || die "git is unavailable"
[[ -n "${BUILDKITE_COMMIT:-}" ]] || die "BUILDKITE_COMMIT is missing"
[[ "${BUILDKITE_COMMIT}" =~ ^[0-9a-f]{40}$ ]] || die "BUILDKITE_COMMIT is not a full Git SHA"
[[ -n "${BUILDKITE_REPO:-}" ]] || die "BUILDKITE_REPO is missing"

expected_repo="git@github.com:1-vps/hostpanel.git"
[[ "$BUILDKITE_REPO" == "$expected_repo" ]] || {
  die "build repository is not the exact reviewed SSH repository"
}
origin_repo="$(git remote get-url origin)"
[[ "$origin_repo" == "$expected_repo" ]] || {
  die "checkout origin is not the exact reviewed SSH repository"
}

actual_sha="$(git rev-parse HEAD)"
[[ "$actual_sha" == "$BUILDKITE_COMMIT" ]] || {
  die "expected $BUILDKITE_COMMIT, checked out $actual_sha"
}
git cat-file -e "${BUILDKITE_COMMIT}^{commit}"

git diff --exit-code -- .
git diff --cached --exit-code -- .
status="$(git status --porcelain=v1 --untracked-files=all)"
[[ -z "$status" ]] || die "checkout contains modified, staged, or untracked files"

printf 'Verified exact Buildkite checkout: %s\n' "$actual_sha"
