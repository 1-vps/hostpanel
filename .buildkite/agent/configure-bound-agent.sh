#!/usr/bin/env bash
set -euo pipefail

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
unset BASH_ENV ENV PYTHONPATH PYTHONHOME LD_PRELOAD LD_LIBRARY_PATH \
  GIT_CONFIG_GLOBAL GIT_CONFIG_SYSTEM GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE \
  GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_EXEC_PATH \
  GIT_TEMPLATE_DIR GIT_CEILING_DIRECTORIES GIT_NO_REPLACE_OBJECTS

fail() {
  printf 'HostPanel Buildkite bound-agent configuration failed: %s\n' "$*" >&2
  exit 1
}

[[ "$(id -u)" == "0" ]] || fail "run as root"

pipeline_id=""
source_commit_expected=""
start_agent="no"
base_args=()
while (($#)); do
  case "$1" in
    --pipeline-id)
      (($# >= 2)) || fail "--pipeline-id requires a value"
      pipeline_id="$2"
      shift 2
      ;;
    --source-commit)
      (($# >= 2)) || fail "--source-commit requires a value"
      source_commit_expected="$2"
      shift 2
      ;;
    --start)
      start_agent="yes"
      shift
      ;;
    *)
      base_args+=("$1")
      shift
      ;;
  esac
done

[[ "$start_agent" == "yes" ]] || {
  fail "--start is required so the one-time registration token cannot remain staged on disk"
}
[[ "$source_commit_expected" =~ ^[0-9a-f]{40}$ ]] || {
  fail "--source-commit must be the externally reviewed full lowercase 40-hex commit SHA"
}

/usr/bin/python3 - "$pipeline_id" <<'PY'
import sys
import uuid
value = sys.argv[1]
try:
    parsed = uuid.UUID(value)
except (ValueError, AttributeError) as exc:
    raise SystemExit("--pipeline-id must be a canonical UUID") from exc
if str(parsed) != value.lower():
    raise SystemExit("--pipeline-id must use canonical UUID form")
PY

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
script_root="$(cd "$script_dir/../.." && pwd -P)"
source_verifier_relative=.buildkite/operator/verify-provisioning-source.py

# This operator-invoked wrapper is the bootstrap trust anchor. Before executing
# or installing any other repository file as root, use only system binaries plus
# the externally recorded commit SHA to build a root-only snapshot from the Git
# object database. Ambient Git configuration, hooks, fsmonitor and replacement
# objects are disabled for every bootstrap Git invocation.
trusted_git() {
  /usr/bin/env -i \
    PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    HOME=/nonexistent \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    GIT_CONFIG_GLOBAL=/dev/null \
    GIT_CONFIG_SYSTEM=/dev/null \
    GIT_CONFIG_NOSYSTEM=1 \
    GIT_TERMINAL_PROMPT=0 \
    GIT_OPTIONAL_LOCKS=0 \
    GIT_NO_REPLACE_OBJECTS=1 \
    /usr/bin/git \
      -c "safe.directory=$script_root" \
      -c core.hooksPath=/dev/null \
      -c core.fsmonitor=false \
      -C "$script_root" \
      "$@"
}

bootstrap_top="$(trusted_git rev-parse --show-toplevel)" || fail "cannot resolve provisioning repository"
[[ "$(readlink -f -- "$bootstrap_top")" == "$script_root" ]] || fail "bound wrapper is not at the exact Git top level"
[[ "$(trusted_git remote get-url origin)" == "git@github.com:1-vps/hostpanel.git" ]] || fail "origin is not the reviewed HostPanel SSH repository"
bootstrap_head="$(trusted_git rev-parse --verify 'HEAD^{commit}')" || fail "cannot resolve provisioning HEAD"
[[ "$bootstrap_head" == "$source_commit_expected" ]] || fail "HEAD does not equal the externally reviewed commit before trusted snapshot creation"
bootstrap_remote_main="$(trusted_git rev-parse --verify 'refs/remotes/origin/main^{commit}')" || fail "cannot resolve origin/main"
[[ "$bootstrap_remote_main" == "$source_commit_expected" ]] || fail "origin/main does not equal the externally reviewed commit before trusted snapshot creation"

source_paths=(
  .buildkite/agent/configure-agent.sh
  .buildkite/agent/configure-bound-agent.sh
  .buildkite/agent/pre-bootstrap
  .buildkite/agent/environment-checkout-policy
  .buildkite/agent/checkout-private-repo
  .buildkite/agent/pre-command-pipeline-id
  .buildkite/agent/upload-trusted-pipeline.sh
  .buildkite/agent/30-hostpanel-ephemeral.conf
  .buildkite/pipeline.yml
)
trusted_source_paths=("$source_verifier_relative" "${source_paths[@]}")
trusted_source_root="$(mktemp -d /run/hostpanel-buildkite-source.XXXXXX)" || fail "cannot create trusted source directory"
chmod 0700 "$trusted_source_root"
cleanup_trusted_source() {
  if [[ -n "${trusted_source_root:-}" ]]; then
    rm -rf -- "$trusted_source_root"
  fi
}
trap cleanup_trusted_source EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

for source_path in "${trusted_source_paths[@]}"; do
  reviewed_blob="$(trusted_git rev-parse "$source_commit_expected:$source_path")" || {
    fail "reviewed provisioning blob is unavailable: $source_path"
  }
  [[ "$reviewed_blob" =~ ^[0-9a-f]{40}$ ]] || fail "reviewed provisioning blob is not canonical: $source_path"
  trusted_path="$trusted_source_root/$source_path"
  install -d -o root -g root -m 0700 "$(dirname "$trusted_path")"
  trusted_git cat-file blob "$reviewed_blob" >"$trusted_path" || {
    fail "cannot materialize reviewed provisioning source: $source_path"
  }
  chmod 0500 "$trusted_path"
  materialized_blob="$(trusted_git hash-object --no-filters -- "$trusted_path")" || {
    fail "cannot hash materialized provisioning source: $source_path"
  }
  [[ "$materialized_blob" == "$reviewed_blob" ]] || {
    fail "materialized provisioning source does not match reviewed commit: $source_path"
  }
done

trusted_verifier="$trusted_source_root/$source_verifier_relative"
trusted_agent_script="$trusted_source_root/.buildkite/agent/configure-agent.sh"
trusted_agent_dir="$trusted_source_root/.buildkite/agent"
verify_args=(--repository "$script_root" --expected-commit "$source_commit_expected")
for source_path in "${source_paths[@]}"; do
  verify_args+=(--source-path "$source_path")
done
source_commit="$(/usr/bin/python3 "$trusted_verifier" "${verify_args[@]}")"
[[ "$source_commit" == "$source_commit_expected" ]] || {
  fail "verified source does not equal the externally reviewed commit"
}

# Never stage one-time credentials while a previous agent session is active.
# Official Ubuntu installation keeps installation and service start separate,
# but this also fails closed for contaminated or accidentally reused images.
if systemctl is-active --quiet buildkite-agent.service; then
  systemctl stop buildkite-agent.service
fi
if systemctl is-active --quiet buildkite-agent.service; then
  fail "a pre-existing Buildkite agent service remained active before provisioning"
fi

token_destination=/etc/buildkite-agent/agent-token
checkout_key_destination=/run/hostpanel-buildkite/checkout-deploy-key
cleanup_staged_credentials() {
  rm -f -- "$token_destination" "$checkout_key_destination"
  cleanup_trusted_source
}
trap cleanup_staged_credentials EXIT

# From this point onward no repository-worktree file is executed or installed as
# root. configure-agent.sh resolves all of its hook/pipeline inputs relative to
# this root-only snapshot, so its complete provisioning source is immutable for
# the duration of the privileged operation.
"$trusted_agent_script" "${base_args[@]}"

policy_dir=/etc/buildkite-agent/hostpanel-policy
configured_queue="$(<"$policy_dir/queue")"
if [[ "$configured_queue" != "hostpanel-upload" ]]; then
  install -d -o buildkite-agent -g buildkite-agent -m 0700 /run/hostpanel-buildkite
  [[ "$(stat -c '%U:%G:%a' /run/hostpanel-buildkite)" == 'buildkite-agent:buildkite-agent:700' ]] || {
    fail "runner checkout credential directory ownership or mode is unsafe"
  }
fi
if [[ "$configured_queue" == "hostpanel-ci" ]]; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get install -y -qq --no-install-recommends python3-yaml
  /usr/bin/python3 -c 'import yaml'
fi

pipeline_id_file="$policy_dir/pipeline-id"
source_commit_file="$policy_dir/source-commit"
[[ -d "$policy_dir" && ! -L "$policy_dir" ]] || fail "base agent policy directory is unsafe"
printf '%s\n' "${pipeline_id,,}" >"$pipeline_id_file"
printf '%s\n' "$source_commit" >"$source_commit_file"
chown root:buildkite-agent "$pipeline_id_file" "$source_commit_file"
chmod 0440 "$pipeline_id_file" "$source_commit_file"
install -o root -g buildkite-agent -m 0550 \
  "$trusted_agent_dir/pre-command-pipeline-id" \
  /etc/buildkite-agent/hooks/pre-command
install -o root -g root -m 0644 \
  "$trusted_agent_dir/30-hostpanel-ephemeral.conf" \
  /etc/systemd/system/buildkite-agent.service.d/30-hostpanel-ephemeral.conf

verified_after="$(/usr/bin/python3 "$trusted_verifier" "${verify_args[@]}")"
[[ "$verified_after" == "$source_commit_expected" ]] || {
  fail "source commit changed during provisioning"
}
cmp -s "$trusted_agent_dir/pre-command-pipeline-id" /etc/buildkite-agent/hooks/pre-command || {
  fail "installed pre-command hook does not match the trusted reviewed snapshot"
}
cmp -s "$trusted_agent_dir/30-hostpanel-ephemeral.conf" \
  /etc/systemd/system/buildkite-agent.service.d/30-hostpanel-ephemeral.conf || {
  fail "installed ephemeral service policy does not match the trusted reviewed snapshot"
}

systemctl daemon-reload
systemd-analyze verify buildkite-agent.service >/dev/null
systemctl restart buildkite-agent.service
systemctl is-active --quiet buildkite-agent.service || fail "Buildkite service is not active after registration"
# The service namespace has ProtectSystem=full. Remove the root-owned token
# from this bound root wrapper outside that namespace after registration.
rm -f -- "$token_destination"
[[ ! -e "$token_destination" ]] || fail "registration token file survived bound-agent startup"
cleanup_trusted_source
trusted_source_root=""

trap - EXIT HUP INT TERM
printf 'Bound and started Buildkite agent for pipeline %s from externally reviewed main commit %s\n' \
  "${pipeline_id,,}" "$source_commit_expected"
