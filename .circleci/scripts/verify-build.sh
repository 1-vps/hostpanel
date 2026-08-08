#!/usr/bin/env bash
set -euo pipefail

die() {
  printf 'CircleCI validation failed: %s\n' "$*" >&2
  exit 1
}

[[ "${CIRCLECI:-}" == "true" ]] || die "CIRCLECI=true is required"
[[ "${CIRCLE_SHA1:-}" =~ ^[0-9a-f]{40}$ ]] || die "CIRCLE_SHA1 is not a full Git SHA"
[[ "${HP_CIRCLECI_REVISION:-}" == "$CIRCLE_SHA1" ]] || die "pipeline revision differs from CIRCLE_SHA1"
[[ "${HP_CIRCLECI_REPOSITORY_OWNER:-}" == "1-vps" ]] || die "unexpected repository owner"
[[ "${HP_CIRCLECI_REPOSITORY_NAME:-}" == "hostpanel" ]] || die "unexpected repository name"
[[ "${HP_CIRCLECI_INTEGRATION:-}" == "github_app" ]] || die "GitHub App integration is required"
[[ "${HP_CIRCLECI_CONFIG_REF:-}" == "refs/heads/main" ]] || die "config is not sourced from main"

actual_sha="$(git rev-parse HEAD)"
[[ "$actual_sha" == "$CIRCLE_SHA1" ]] || die "checked-out commit differs from CIRCLE_SHA1"
git cat-file -e "${CIRCLE_SHA1}^{commit}" || die "checked-out SHA is not a commit"
[[ -z "$(git status --porcelain --untracked-files=all)" ]] || die "checkout is modified"

if [[ -n "${SSH_AUTH_SOCK:-}" || -n "${SSH_AGENT_PID:-}" ]]; then
  die "checkout SSH agent is still exposed"
fi
if [[ -d "$HOME/.ssh" ]] && grep -RIl -- 'PRIVATE KEY' "$HOME/.ssh" >/dev/null 2>&1; then
  die "checkout private key remains on worker"
fi
