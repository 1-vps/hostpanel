#!/usr/bin/env bash
set -euo pipefail

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
unset BASH_ENV ENV PYTHONPATH PYTHONHOME LD_PRELOAD LD_LIBRARY_PATH

fail() {
  printf 'HostPanel Buildkite agent smoke test failed: %s\n' "$*" >&2
  exit 1
}

(($# == 3)) || fail "usage: $0 QUEUE PIPELINE_SLUG PIPELINE_ID"
queue="$1"
pipeline_slug="$2"
pipeline_id="$3"
[[ "$queue" =~ ^hostpanel-(upload|ci|qemu)$ ]] || fail "invalid queue"
[[ "$pipeline_slug" =~ ^[a-z0-9][a-z0-9-]{0,99}$ ]] || fail "invalid pipeline slug"
python3 - "$pipeline_id" <<'PY'
import sys, uuid
value = sys.argv[1]
try:
    parsed = uuid.UUID(value)
except ValueError as exc:
    raise SystemExit("pipeline ID is not a UUID") from exc
if str(parsed) != value.lower():
    raise SystemExit("pipeline ID is not canonical")
PY

[[ "$(id -u)" == "0" ]] || fail "run as root"
for command in buildkite-agent python3 ssh ssh-keygen git timeout findmnt swapon; do
  command -v "$command" >/dev/null 2>&1 || fail "$command is unavailable"
done

version_text="$(buildkite-agent --version 2>&1)"
version="$(printf '%s\n' "$version_text" | grep -Eo '[0-9]+\.[0-9]+\.[0-9]+' | head -n1)"
[[ -n "$version" ]] || fail "cannot parse Buildkite agent version"
python3 - "$version" <<'PYVER'
import sys
if tuple(int(part) for part in sys.argv[1].split(".")) < (3, 134, 0):
    raise SystemExit("Buildkite agent must be 3.134.0 or newer")
PYVER

config=/etc/buildkite-agent/buildkite-agent.cfg
policy=/etc/buildkite-agent/hostpanel-policy
[[ -f "$config" && ! -L "$config" ]] || fail "agent config is missing or unsafe"
for item in queue pipeline-slug pipeline-id source-commit; do
  [[ -f "$policy/$item" && ! -L "$policy/$item" ]] || fail "policy $item is missing or unsafe"
done
[[ "$(<"$policy/queue")" == "$queue" ]] || fail "queue policy mismatch"
[[ "$(<"$policy/pipeline-slug")" == "$pipeline_slug" ]] || fail "pipeline slug policy mismatch"
[[ "$(<"$policy/pipeline-id")" == "${pipeline_id,,}" ]] || fail "pipeline ID policy mismatch"

for required in \
  'token="fd://3"' \
  'no-command-eval=true' \
  'checkout-override-mode="strict"' \
  'no-plugins=true' \
  'no-local-hooks=true' \
  'no-git-submodules=true' \
  'no-ssh-keyscan=true' \
  'verification-failure-behavior="block"' \
  'disconnect-after-job=true'
do
  grep -Fqx "$required" "$config" || fail "missing agent setting: $required"
done
grep -Fq 'git-commit-verification=' "$config" && {
  fail "native git commit verification must not be claimed when the trusted checkout hook overrides Git"
}
grep -Fqx "queue=\"$queue\"" "$config" || fail "configured queue does not match"
grep -Fqx 'allowed-repositories="^git@github\.com:1-vps/hostpanel\.git$"' "$config" || {
  fail "repository allowlist is not exact"
}

hardening=/etc/systemd/system/buildkite-agent.service.d/20-hostpanel-hardening.conf
ephemeral=/etc/systemd/system/buildkite-agent.service.d/30-hostpanel-ephemeral.conf
for file in "$hardening" "$ephemeral"; do
  [[ -f "$file" && ! -L "$file" ]] || fail "systemd hardening file is missing or unsafe: $file"
done
for required in \
  'OpenFile=' \
  'OpenFile=/etc/buildkite-agent/agent-token:buildkite-agent-token:read-only' \
  'ExecStartPost=+/usr/bin/rm -f /etc/buildkite-agent/agent-token' \
  'LimitCORE=0'
do
  grep -Fqx "$required" "$hardening" || fail "missing token-isolation setting: $required"
done
grep -Fqx 'Restart=no' "$ephemeral" || fail "ephemeral service must not restart"
grep -Fqx 'ExecStopPost=+/usr/bin/rm -f /etc/buildkite-agent/agent-token /run/hostpanel-buildkite/checkout-deploy-key' "$ephemeral" || {
  fail "ephemeral service lacks stop-time credential cleanup"
}
[[ ! -e /etc/buildkite-agent/agent-token ]] || fail "registration token file remains after startup"

for hook in pre-bootstrap pre-command environment; do
  [[ -x "/etc/buildkite-agent/hooks/$hook" && ! -L "/etc/buildkite-agent/hooks/$hook" ]] || {
    fail "required agent hook is missing or unsafe: $hook"
  }
done

checkout_state="$(
  env -u BUILDKITE_SKIP_CHECKOUT -u BUILDKITE_CLEAN_CHECKOUT \
    bash -c 'source "$1"; printf "%s|%s" "${BUILDKITE_SKIP_CHECKOUT-<unset>}" "${BUILDKITE_CLEAN_CHECKOUT-<unset>}"' \
    bash /etc/buildkite-agent/hooks/environment
)"

python3 - /etc/buildkite-agent/keys/verification.jwks <<'PY'
import json, pathlib, sys
value = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
keys = value.get("keys") if isinstance(value, dict) else None
if not isinstance(keys, list) or not keys:
    raise SystemExit("verification file is not a non-empty JWKS")
if any(not isinstance(item, dict) or "d" in item for item in keys):
    raise SystemExit("verification JWKS contains an invalid or private key")
PY

known_hosts=/var/lib/buildkite-agent/.ssh/known_hosts
[[ -f "$known_hosts" && ! -L "$known_hosts" ]] || fail "GitHub known_hosts is missing or unsafe"
python3 - "$known_hosts" <<'PYHOSTS'
import pathlib, stat, sys
path = pathlib.Path(sys.argv[1])
info = path.stat()
if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
    raise SystemExit("known_hosts ownership or mode is unsafe")
if "github.com ssh-ed25519 " not in path.read_text(encoding="utf-8"):
    raise SystemExit("known_hosts lacks reviewed GitHub Ed25519 key")
PYHOSTS

if [[ "$queue" != "hostpanel-upload" ]]; then
  [[ "$(findmnt -n -o FSTYPE --target /run)" == "tmpfs" ]] || fail "/run is not tmpfs"
  [[ -z "$(swapon --show=NAME --noheadings)" ]] || fail "swap is active on runner worker"
fi

case "$queue" in
  hostpanel-upload)
    grep -Fqx 'skip-checkout=true' "$config" || fail "upload queue must skip Buildkite checkout"
    [[ "$checkout_state" == 'true|<unset>' ]] || fail "upload checkout environment policy mismatch"
    [[ ! -e /etc/buildkite-agent/hooks/checkout ]] || fail "upload queue must not have private checkout hook"
    [[ ! -e /run/hostpanel-buildkite/checkout-deploy-key ]] || fail "upload queue contains GitHub checkout key"
    for file in \
      /usr/local/libexec/hostpanel-upload-pipeline \
      /etc/buildkite-agent/hostpanel-pipeline.yml \
      "$policy/pipeline-sha256" \
      /etc/buildkite-agent/keys/signing.jwks
    do
      [[ -f "$file" && ! -L "$file" ]] || fail "upload queue lacks trusted asset: $file"
    done
    grep -Fq 'signing-jwks-file=' "$config" || fail "upload queue lacks signing configuration"
    id -nG buildkite-agent | tr ' ' '\n' | grep -Fxq docker && fail "upload agent must not be in docker group"
    id -nG buildkite-agent | tr ' ' '\n' | grep -Fxq kvm && fail "upload agent must not be in kvm group"
    ;;
  hostpanel-ci)
    grep -Fqx 'skip-checkout=false' "$config" || fail "CI queue must perform trusted checkout"
    [[ "$checkout_state" == 'false|true' ]] || fail "CI checkout environment policy mismatch"
    [[ -x /etc/buildkite-agent/hooks/checkout && ! -L /etc/buildkite-agent/hooks/checkout ]] || {
      fail "CI queue lacks trusted private-repository checkout hook"
    }
    checkout_key=/run/hostpanel-buildkite/checkout-deploy-key
    [[ -f "$checkout_key" && ! -L "$checkout_key" ]] || fail "CI queue lacks staged one-time checkout key"
    [[ "$(stat -c '%U:%G:%a' "$checkout_key")" == 'buildkite-agent:buildkite-agent:400' ]] || fail "CI checkout key ownership or mode is unsafe"
    ssh-keygen -y -P '' -f "$checkout_key" >/dev/null 2>&1 || fail "CI checkout key is invalid or encrypted"
    [[ ! -e /etc/buildkite-agent/keys/signing.jwks ]] || fail "CI queue contains signing material"
    [[ ! -e /usr/local/libexec/hostpanel-upload-pipeline ]] || fail "CI queue contains uploader"
    id -nG buildkite-agent | tr ' ' '\n' | grep -Fxq docker || fail "CI agent lacks docker group"
    id -nG buildkite-agent | tr ' ' '\n' | grep -Fxq kvm && fail "CI agent must not be in kvm group"
    systemctl is-active --quiet docker.service || fail "Docker is not active"
    ;;
  hostpanel-qemu)
    grep -Fqx 'skip-checkout=false' "$config" || fail "QEMU queue must perform trusted checkout"
    [[ "$checkout_state" == 'false|true' ]] || fail "QEMU checkout environment policy mismatch"
    [[ -x /etc/buildkite-agent/hooks/checkout && ! -L /etc/buildkite-agent/hooks/checkout ]] || {
      fail "QEMU queue lacks trusted private-repository checkout hook"
    }
    checkout_key=/run/hostpanel-buildkite/checkout-deploy-key
    [[ -f "$checkout_key" && ! -L "$checkout_key" ]] || fail "QEMU queue lacks staged one-time checkout key"
    [[ "$(stat -c '%U:%G:%a' "$checkout_key")" == 'buildkite-agent:buildkite-agent:400' ]] || fail "QEMU checkout key ownership or mode is unsafe"
    ssh-keygen -y -P '' -f "$checkout_key" >/dev/null 2>&1 || fail "QEMU checkout key is invalid or encrypted"
    [[ ! -e /etc/buildkite-agent/keys/signing.jwks ]] || fail "QEMU queue contains signing material"
    [[ ! -e /usr/local/libexec/hostpanel-upload-pipeline ]] || fail "QEMU queue contains uploader"
    id -nG buildkite-agent | tr ' ' '\n' | grep -Fxq kvm || fail "QEMU agent lacks kvm group"
    id -nG buildkite-agent | tr ' ' '\n' | grep -Fxq docker && fail "QEMU agent must not be in docker group"
    [[ -c /dev/kvm ]] || fail "/dev/kvm is unavailable"
    ;;
esac

systemctl is-enabled --quiet buildkite-agent.service || fail "Buildkite service is not enabled"
systemctl is-active --quiet buildkite-agent.service || fail "Buildkite service is not active"
[[ "$(systemctl show -p Restart --value buildkite-agent.service)" == "no" ]] || {
  fail "Buildkite service restart policy is not disabled"
}
systemd-analyze verify buildkite-agent.service >/dev/null
printf 'HostPanel Buildkite agent smoke test passed: %s / %s / %s\n' \
  "$queue" "$pipeline_slug" "${pipeline_id,,}"
