#!/usr/bin/env bash
set -euo pipefail

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
unset BASH_ENV ENV PYTHONPATH PYTHONHOME LD_PRELOAD LD_LIBRARY_PATH
umask 077

fail() {
  printf 'HostPanel Buildkite control-plane operator failed: %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Usage:
  bootstrap-control-plane.sh --org ORGANIZATION [--apply --confirm-create]
  bootstrap-control-plane.sh --org ORGANIZATION --enable-webhook PIPELINE_SLUG \
    [--apply --confirm-static-bootstrap-signed --confirm-public-deploy-key-added]

Default behavior is plan-only and performs no Buildkite API writes.

Creation mode:
- proves the active Buildkite credential has the required scopes;
- creates the isolated HostPanel cluster and exactly three queues;
- creates the pipeline with the reviewed static no-checkout bootstrap;
- keeps GitHub activity quarantined with trigger_mode=none;
- accepts either an absent or already-present GitHub webhook;
- starts no agents and activates no GitHub build triggers.

Activation mode:
- proves scopes again;
- requires the static Pipeline Settings to already be signed;
- proves zero build history and no connected HostPanel agents;
- accepts an existing valid Buildkite GitHub webhook, or creates one only if absent;
- keeps trigger_mode=none through webhook verification;
- PATCHes provider_settings to the reviewed active trigger_mode=code policy;
- starts no agents and creates no registration tokens.

This tool never prints API tokens, creates agent tokens, starts agents, changes
branch protection, or automatically deletes partially-created resources.
EOF
}

org=""
apply=false
confirm_create=false
confirm_static_bootstrap_signed=false
confirm_public_deploy_key_added=false
enable_webhook_slug=""

while (($#)); do
  case "$1" in
    --org)
      (($# >= 2)) || fail "--org requires a value"
      org="$2"
      shift 2
      ;;
    --apply)
      apply=true
      shift
      ;;
    --confirm-create)
      confirm_create=true
      shift
      ;;
    --enable-webhook)
      (($# >= 2)) || fail "--enable-webhook requires a pipeline slug"
      enable_webhook_slug="$2"
      shift 2
      ;;
    --confirm-static-bootstrap-signed)
      confirm_static_bootstrap_signed=true
      shift
      ;;
    --confirm-public-deploy-key-added)
      confirm_public_deploy_key_added=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "unknown argument: $1"
      ;;
  esac
done

[[ "$org" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]] || fail "invalid --org"
if [[ -n "$enable_webhook_slug" ]]; then
  [[ "$enable_webhook_slug" =~ ^[a-z0-9][a-z0-9-]{0,99}$ ]] || {
    fail "invalid --enable-webhook slug"
  }
  [[ "$confirm_create" == "false" ]] || {
    fail "--confirm-create is not valid with --enable-webhook"
  }
else
  [[ "$confirm_static_bootstrap_signed" == "false" ]] || {
    fail "--confirm-static-bootstrap-signed requires --enable-webhook"
  }
  [[ "$confirm_public_deploy_key_added" == "false" ]] || {
    fail "--confirm-public-deploy-key-added requires --enable-webhook"
  }
fi

repository='git@github.com:1-vps/hostpanel.git'
cluster_name='HostPanel'
pipeline_name='HostPanel'
queue_upload='hostpanel-upload'
queue_ci='hostpanel-ci'
queue_qemu='hostpanel-qemu'

read -r -d '' static_bootstrap <<'YAML' || true
steps:
  - label: ":pipeline: Upload reviewed pipeline"
    key: "upload-reviewed-pipeline"
    command: "/usr/local/libexec/hostpanel-upload-pipeline"
    checkout:
      skip: true
    agents:
      queue: "hostpanel-upload"
YAML

print_create_plan() {
  cat <<EOF
HostPanel Buildkite control-plane creation plan
organization: $org
repository:   $repository

No agents are started by this tool.

1. Switch to the reviewed organization and prove required access-token scopes.
2. Refuse duplicate HostPanel clusters or pipelines for the exact repository.
3. Create one isolated HostPanel cluster with no default queue.
4. Create exactly:
   $queue_upload
   $queue_ci
   $queue_qemu
5. Create the pipeline with the reviewed static no-checkout bootstrap.
6. Quarantine all GitHub activity:
   trigger_mode=none
   branch/PR/tag/comment triggers disabled
   commit-status publishing disabled
7. Verify zero build history and no connected HostPanel agents.
8. Accept either webhook state after creation; webhook presence never activates
   builds while trigger_mode=none.

MANDATORY STOP:
Statically sign Pipeline Settings from merged bootstrap commit
e8aa04a6f231fbf7d4fa0e040e199c6b6ba177aa and add only the public checkout
deploy key to GitHub. Then use --enable-webhook in a separate invocation.
EOF
}

print_activation_plan() {
  cat <<EOF
HostPanel Buildkite trigger activation plan
organization: $org
pipeline:     $enable_webhook_slug
repository:   $repository

No agents are started by this tool.

Preconditions:
- required Buildkite API scopes are proven from /access-token;
- Pipeline Settings contain exactly the reviewed static no-checkout bootstrap;
- the bootstrap is statically signed with key id hostpanel-2026-08;
- provider policy is still quarantined with trigger_mode=none;
- build history is empty;
- no HostPanel agent is connected;
- the public checkout deploy key has been added to GitHub.

Apply mode:
1. Reverify all preconditions while still quarantined.
2. If a valid Buildkite webhook already exists, keep it.
3. If no webhook URL exists, create the standard GitHub App webhook.
4. Reverify signed bootstrap + webhook + trigger_mode=none + zero builds.
5. PATCH provider_settings to the reviewed active trigger_mode=code policy.
6. Reverify the signed bootstrap, webhook, active policy, zero build history,
   and no connected HostPanel agents.

Worker provisioning remains a separate reviewed step.
EOF
}

if [[ "$apply" != "true" ]]; then
  if [[ -n "$enable_webhook_slug" ]]; then
    print_activation_plan
  else
    print_create_plan
  fi
  exit 0
fi

if [[ -n "$enable_webhook_slug" ]]; then
  [[ "$confirm_static_bootstrap_signed" == "true" ]] || {
    fail "--enable-webhook with --apply requires --confirm-static-bootstrap-signed"
  }
  [[ "$confirm_public_deploy_key_added" == "true" ]] || {
    fail "--enable-webhook with --apply requires --confirm-public-deploy-key-added"
  }
else
  [[ "$confirm_create" == "true" ]] || fail "--apply requires --confirm-create"
fi

command -v bk >/dev/null 2>&1 || fail "bk CLI is unavailable"
command -v python3 >/dev/null 2>&1 || fail "python3 is unavailable"
python3 -c 'import yaml' >/dev/null 2>&1 || {
  fail "python3-yaml is required to verify Buildkite pipeline configuration"
}

bk auth switch "$org" >/dev/null
bk auth status -o json >/dev/null

access_token_metadata="$(bk api /access-token)"
python3 - "$access_token_metadata" <<'PYSCOPES'
import json
import sys

required = {
    "read_clusters",
    "write_clusters",
    "read_pipelines",
    "write_pipelines",
    "read_builds",
    "read_agents",
}

try:
    payload = json.loads(sys.argv[1])
except json.JSONDecodeError as exc:
    raise SystemExit("bk api /access-token did not return valid JSON") from exc

if not isinstance(payload, dict):
    raise SystemExit("bk api /access-token did not return a JSON object")

scopes = payload.get("scopes")
if not isinstance(scopes, list) or not all(isinstance(item, str) for item in scopes):
    raise SystemExit("bk api /access-token did not return a valid scopes array")

missing = sorted(required - set(scopes))
if missing:
    raise SystemExit(
        "Buildkite credential is missing required scope(s): " + ", ".join(missing)
    )
PYSCOPES
unset access_token_metadata
printf 'Buildkite access-token scope preflight passed.\n'

top_uuid() {
  local raw="$1"
  local label="$2"
  python3 - "$label" "$raw" <<'PYUUID'
import json
import sys
import uuid

label, raw = sys.argv[1:]
try:
    payload = json.loads(raw)
except json.JSONDecodeError as exc:
    raise SystemExit(f"{label} did not return valid JSON") from exc

if not isinstance(payload, dict):
    raise SystemExit(f"{label} did not return a JSON object")

value = payload.get("id")
if not isinstance(value, str):
    value = payload.get("uuid")
if not isinstance(value, str) or not value:
    raise SystemExit(f"{label} did not contain a top-level id/uuid")

try:
    uuid.UUID(value)
except ValueError as exc:
    raise SystemExit(f"{label} id is not a UUID") from exc

print(value)
PYUUID
}

verify_pipeline_state() {
  local expected_state="$1"
  local expected_slug="$2"
  local pipeline_raw="$3"
  local builds_raw="$4"
  local agents_raw="$5"

  python3 - \
    "$expected_state" \
    "$expected_slug" \
    "$repository" \
    "$static_bootstrap" \
    "$pipeline_raw" \
    "$builds_raw" \
    "$agents_raw" <<'PYSTATE'
import base64
import json
import re
import sys
import uuid
import yaml

(
    state,
    expected_slug,
    repository,
    expected_configuration,
    pipeline_raw,
    builds_raw,
    agents_raw,
) = sys.argv[1:]

def load(label: str, raw: str):
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{label} did not return valid JSON") from exc

pipeline = load("pipeline", pipeline_raw)
if not isinstance(pipeline, dict):
    raise SystemExit("pipeline response is not a JSON object")

slug = pipeline.get("slug")
if expected_slug:
    if slug != expected_slug:
        raise SystemExit("pipeline slug does not match the requested target")
elif not isinstance(slug, str) or re.fullmatch(r"[a-z0-9][a-z0-9-]{0,99}", slug) is None:
    raise SystemExit("pipeline returned an unsafe slug")

if pipeline.get("repository") != repository:
    raise SystemExit("pipeline repository is not the reviewed HostPanel repository")

cluster_id = pipeline.get("cluster_id")
if not isinstance(cluster_id, str) or not cluster_id:
    raise SystemExit("pipeline is not assigned to an isolated cluster")
try:
    uuid.UUID(cluster_id)
except ValueError as exc:
    raise SystemExit("pipeline cluster_id is not a UUID") from exc

configuration = pipeline.get("configuration")
if not isinstance(configuration, str):
    raise SystemExit("pipeline configuration is unavailable")
try:
    config = yaml.safe_load(configuration)
    expected_config = yaml.safe_load(expected_configuration)
except yaml.YAMLError as exc:
    raise SystemExit("pipeline configuration is not valid YAML") from exc

if not isinstance(config, dict) or set(config) != {"steps"}:
    raise SystemExit("pipeline configuration must contain only steps")
steps = config.get("steps")
if not isinstance(steps, list) or len(steps) != 1 or not isinstance(steps[0], dict):
    raise SystemExit("pipeline configuration must contain exactly one bootstrap step")

step = steps[0]
static_fields = {
    "label": ":pipeline: Upload reviewed pipeline",
    "key": "upload-reviewed-pipeline",
    "command": "/usr/local/libexec/hostpanel-upload-pipeline",
    "checkout": {"skip": True},
    "agents": {"queue": "hostpanel-upload"},
}
for key, expected in static_fields.items():
    if step.get(key) != expected:
        raise SystemExit(f"static bootstrap field mismatch: {key}")

signed = state in {"quarantine-signed", "quarantine-webhook", "active-signed"}
if not signed:
    if config != expected_config:
        raise SystemExit("unsigned pipeline configuration is not the exact reviewed bootstrap")
else:
    allowed = set(static_fields) | {"signature"}
    if set(step) - allowed:
        raise SystemExit("signed bootstrap contains unreviewed step fields")
    signature = step.get("signature")
    if not isinstance(signature, dict):
        raise SystemExit("static bootstrap is not signed")
    if set(signature) != {"algorithm", "signed_fields", "value"}:
        raise SystemExit("static bootstrap signature shape is unexpected")
    if signature.get("algorithm") != "EdDSA":
        raise SystemExit("static bootstrap signature algorithm is not EdDSA")
    signed_fields = signature.get("signed_fields")
    if not isinstance(signed_fields, list) or not {
        "command",
        "repository_url",
    }.issubset(set(signed_fields)):
        raise SystemExit("static bootstrap signature does not cover command and repository")
    value = signature.get("value")
    if not isinstance(value, str) or value.count(".") != 2:
        raise SystemExit("static bootstrap signature value is not compact JWS")
    parts = value.split(".")
    if parts[1] != "":
        raise SystemExit("static bootstrap signature is not detached JWS")
    try:
        header_raw = parts[0] + "=" * (-len(parts[0]) % 4)
        header = json.loads(base64.urlsafe_b64decode(header_raw).decode("utf-8"))
    except Exception as exc:
        raise SystemExit("static bootstrap signature header is invalid") from exc
    if not isinstance(header, dict) or header.get("alg") != "EdDSA":
        raise SystemExit("static bootstrap JWS algorithm mismatch")
    if header.get("kid") != "hostpanel-2026-08":
        raise SystemExit("static bootstrap JWS key ID mismatch")

provider = pipeline.get("provider")
if not isinstance(provider, dict) or provider.get("id") != "github":
    raise SystemExit("pipeline is not using the GitHub provider")
settings = provider.get("settings")
if not isinstance(settings, dict):
    raise SystemExit("pipeline GitHub provider settings are unavailable")

quarantine = {
    "build_branches": False,
    "build_pull_requests": False,
    "build_pull_request_forks": False,
    "build_tags": False,
    "publish_commit_status": False,
    "publish_commit_status_per_step": False,
    "build_issue_comment_created": False,
    "build_pull_request_ready_for_review": False,
    "build_pull_request_merge_commits": False,
    "skip_builds_for_existing_commits": False,
    "skip_pull_request_builds_for_existing_commits": False,
    "separate_pull_request_statuses": True,
    "trigger_mode": "none",
}
active = {
    "build_branches": True,
    "build_pull_requests": True,
    "build_pull_request_forks": False,
    "build_tags": False,
    "publish_commit_status": True,
    "publish_commit_status_per_step": False,
    "build_issue_comment_created": True,
    "issue_comment_command_word": "/bk",
    "issue_comment_match_mode": "exact",
    "build_pull_request_ready_for_review": True,
    "build_pull_request_merge_commits": False,
    "skip_builds_for_existing_commits": False,
    "skip_pull_request_builds_for_existing_commits": False,
    "separate_pull_request_statuses": True,
    "trigger_mode": "code",
}
expected_settings = active if state == "active-signed" else quarantine
for key, expected in expected_settings.items():
    if settings.get(key) != expected:
        raise SystemExit(f"pipeline GitHub provider setting mismatch: {key}")

webhook_url = provider.get("webhook_url")
if webhook_url in (None, ""):
    webhook_url = ""
elif not isinstance(webhook_url, str) or not webhook_url.startswith(
    "https://webhook.buildkite.com/deliver/"
):
    raise SystemExit("pipeline exposes an unexpected webhook URL")

if state in {"quarantine-webhook", "active-signed"} and not webhook_url:
    raise SystemExit("pipeline does not expose the required Buildkite webhook URL")

builds = load("bk build list", builds_raw)
if isinstance(builds, list):
    build_items = builds
elif isinstance(builds, dict) and isinstance(builds.get("builds"), list):
    build_items = builds["builds"]
else:
    raise SystemExit("bk build list returned an unrecognized JSON shape")
if build_items:
    raise SystemExit("pipeline already has build history; inspect before activation")

agents = load("bk agent list", agents_raw)
if isinstance(agents, list):
    agent_items = agents
elif isinstance(agents, dict) and isinstance(agents.get("agents"), list):
    agent_items = agents["agents"]
else:
    raise SystemExit("bk agent list returned an unrecognized JSON shape")
if len(agent_items) >= 1000:
    raise SystemExit(
        "agent inventory reached the query limit; cannot prove the target cluster is empty"
    )
for agent in agent_items:
    if not isinstance(agent, dict):
        raise SystemExit("bk agent list contained a non-object agent")
    web_url = agent.get("web_url")
    if isinstance(web_url, str) and f"/clusters/{cluster_id}/" in web_url:
        raise SystemExit("an agent is already connected to the HostPanel cluster")

print(slug)
PYSTATE
}

pipeline_snapshot() {
  local slug="$1"
  bk pipeline view "$org/$slug" -o json
}

build_snapshot() {
  local slug="$1"
  bk build list --pipeline "$org/$slug" --limit 1 -o json
}

agent_snapshot() {
  bk agent list --limit 1000 -o json
}

if [[ -n "$enable_webhook_slug" ]]; then
  pipeline_json="$(pipeline_snapshot "$enable_webhook_slug")"
  build_list="$(build_snapshot "$enable_webhook_slug")"
  agent_list="$(agent_snapshot)"
  verify_pipeline_state \
    "quarantine-signed" \
    "$enable_webhook_slug" \
    "$pipeline_json" \
    "$build_list" \
    "$agent_list" >/dev/null

  webhook_url="$(
    python3 - "$pipeline_json" <<'PYWEBHOOK'
import json
import sys
payload = json.loads(sys.argv[1])
provider = payload.get("provider") if isinstance(payload, dict) else None
value = provider.get("webhook_url") if isinstance(provider, dict) else None
if value in (None, ""):
    print("")
elif isinstance(value, str) and value.startswith("https://webhook.buildkite.com/deliver/"):
    print(value)
else:
    raise SystemExit("pipeline exposes an unexpected webhook URL")
PYWEBHOOK
  )"

  if [[ -z "$webhook_url" ]]; then
    bk api --method POST "/pipelines/$enable_webhook_slug/webhook" >/dev/null
  fi
  unset webhook_url

  quarantined_pipeline="$(pipeline_snapshot "$enable_webhook_slug")"
  quarantined_builds="$(build_snapshot "$enable_webhook_slug")"
  quarantined_agents="$(agent_snapshot)"
  verify_pipeline_state \
    "quarantine-webhook" \
    "$enable_webhook_slug" \
    "$quarantined_pipeline" \
    "$quarantined_builds" \
    "$quarantined_agents" >/dev/null

  active_payload="$(
    python3 <<'PYACTIVE'
import json
print(json.dumps({
    "provider_settings": {
        "build_branches": True,
        "build_pull_requests": True,
        "build_pull_request_forks": False,
        "build_tags": False,
        "publish_commit_status": True,
        "publish_commit_status_per_step": False,
        "build_issue_comment_created": True,
        "issue_comment_command_word": "/bk",
        "issue_comment_match_mode": "exact",
        "build_pull_request_ready_for_review": True,
        "build_pull_request_merge_commits": False,
        "skip_builds_for_existing_commits": False,
        "skip_pull_request_builds_for_existing_commits": False,
        "separate_pull_request_statuses": True,
        "trigger_mode": "code",
    }
}))
PYACTIVE
  )"

  bk api \
    --method PATCH \
    "/pipelines/$enable_webhook_slug" \
    --data "$active_payload" >/dev/null
  unset active_payload

  active_pipeline="$(pipeline_snapshot "$enable_webhook_slug")"
  active_builds="$(build_snapshot "$enable_webhook_slug")"
  active_agents="$(agent_snapshot)"
  verify_pipeline_state \
    "active-signed" \
    "$enable_webhook_slug" \
    "$active_pipeline" \
    "$active_builds" \
    "$active_agents" >/dev/null

  cat <<EOF
HostPanel Buildkite GitHub trigger policy activated and verified.

organization=$org
pipeline_slug=$enable_webhook_slug
repository=$repository
trigger_mode=code

No agents were started. Provision only reviewed disposable workers from merged
bootstrap commit e8aa04a6f231fbf7d4fa0e040e199c6b6ba177aa, then emit a fresh
ready_for_review event on the unchanged exact PR head.
EOF
  exit 0
fi

cluster_list="$(bk cluster list -o json)"
pipeline_list="$(bk pipeline list --repository "$repository" -o json)"

python3 - "$cluster_name" "$repository" "$cluster_list" "$pipeline_list" <<'PYDUP'
import json
import sys

cluster_name, repository, cluster_raw, pipeline_raw = sys.argv[1:]

def load(label: str, raw: str):
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{label} did not return valid JSON") from exc

def dicts(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from dicts(child)

clusters = load("bk cluster list", cluster_raw)
pipelines = load("bk pipeline list", pipeline_raw)

for item in dicts(clusters):
    name = item.get("name")
    if isinstance(name, str) and name.casefold() == cluster_name.casefold():
        raise SystemExit(
            "an existing HostPanel cluster was found; inspect it instead of creating a duplicate"
        )

for item in dicts(pipelines):
    name = item.get("name")
    if isinstance(name, str) and name.casefold() == cluster_name.casefold():
        raise SystemExit(
            "an existing HostPanel pipeline name was found; inspect it instead of creating a duplicate"
        )
    if item.get("repository") == repository or item.get("repository_url") == repository:
        raise SystemExit(
            "an existing pipeline for the HostPanel repository was found; inspect it instead of creating a duplicate"
        )
PYDUP

cluster_uuid=""
pipeline_uuid=""
pipeline_slug=""
upload_queue_uuid=""
ci_queue_uuid=""
qemu_queue_uuid=""

on_error() {
  local rc=$?
  printf 'Control-plane creation stopped with status %s.\n' "$rc" >&2
  printf 'Created resource identifiers, if any:\n' >&2
  printf '  cluster_uuid=%s\n' "${cluster_uuid:-}" >&2
  printf '  upload_queue_uuid=%s\n' "${upload_queue_uuid:-}" >&2
  printf '  ci_queue_uuid=%s\n' "${ci_queue_uuid:-}" >&2
  printf '  qemu_queue_uuid=%s\n' "${qemu_queue_uuid:-}" >&2
  printf '  pipeline_uuid=%s\n' "${pipeline_uuid:-}" >&2
  printf '  pipeline_slug=%s\n' "${pipeline_slug:-}" >&2
  printf 'No automatic cleanup was attempted; inspect Buildkite before changing partial resources.\n' >&2
  exit "$rc"
}
trap on_error ERR

cluster_json="$(
  bk cluster create \
    --name "$cluster_name" \
    --description 'Disposable hardened HostPanel CI' \
    -o json
)"
cluster_uuid="$(top_uuid "$cluster_json" 'bk cluster create')"

upload_queue_json="$(
  bk queue create "$cluster_uuid" \
    --key "$queue_upload" \
    --description 'Trusted no-checkout pipeline signer/uploader' \
    -o json
)"
upload_queue_uuid="$(top_uuid "$upload_queue_json" 'bk queue create hostpanel-upload')"

ci_queue_json="$(
  bk queue create "$cluster_uuid" \
    --key "$queue_ci" \
    --description 'Disposable repository CI workers' \
    -o json
)"
ci_queue_uuid="$(top_uuid "$ci_queue_json" 'bk queue create hostpanel-ci')"

qemu_queue_json="$(
  bk queue create "$cluster_uuid" \
    --key "$queue_qemu" \
    --description 'Disposable KVM post-merge acceptance workers' \
    -o json
)"
qemu_queue_uuid="$(top_uuid "$qemu_queue_json" 'bk queue create hostpanel-qemu')"

cluster_view="$(bk cluster view "$cluster_uuid" -o json)"
queue_list="$(bk queue list "$cluster_uuid" -o json)"
python3 - "$cluster_uuid" "$cluster_view" "$queue_list" <<'PYCLUSTER'
import json
import sys

cluster_id, cluster_raw, queues_raw = sys.argv[1:]
try:
    cluster = json.loads(cluster_raw)
    queues = json.loads(queues_raw)
except json.JSONDecodeError as exc:
    raise SystemExit("cluster/queue verification did not return valid JSON") from exc

if not isinstance(cluster, dict) or cluster.get("id") != cluster_id:
    raise SystemExit("created cluster identity mismatch")
if cluster.get("default_queue_id") is not None:
    raise SystemExit("created HostPanel cluster unexpectedly has a default queue")

if not isinstance(queues, list):
    if isinstance(queues, dict) and isinstance(queues.get("queues"), list):
        queues = queues["queues"]
    else:
        raise SystemExit("bk queue list returned an unrecognized JSON shape")

keys = []
for queue in queues:
    if not isinstance(queue, dict) or not isinstance(queue.get("key"), str):
        raise SystemExit("bk queue list contained an invalid queue object")
    keys.append(queue["key"])

if sorted(keys) != ["hostpanel-ci", "hostpanel-qemu", "hostpanel-upload"]:
    raise SystemExit("HostPanel cluster does not contain exactly the reviewed queue keys")
PYCLUSTER

pipeline_payload="$(
  python3 - "$pipeline_name" "$cluster_uuid" "$repository" "$static_bootstrap" <<'PYPAYLOAD'
import json
import sys

name, cluster_id, repository, configuration = sys.argv[1:]
print(json.dumps({
    "name": name,
    "cluster_id": cluster_id,
    "repository": repository,
    "configuration": configuration,
    "provider_settings": {
        "build_branches": False,
        "build_pull_requests": False,
        "build_pull_request_forks": False,
        "build_tags": False,
        "publish_commit_status": False,
        "publish_commit_status_per_step": False,
        "build_issue_comment_created": False,
        "build_pull_request_ready_for_review": False,
        "build_pull_request_merge_commits": False,
        "skip_builds_for_existing_commits": False,
        "skip_pull_request_builds_for_existing_commits": False,
        "separate_pull_request_statuses": True,
        "trigger_mode": "none",
    },
}))
PYPAYLOAD
)"

pipeline_json="$(bk api --method POST /pipelines --data "$pipeline_payload")"

read -r pipeline_uuid pipeline_slug < <(
  python3 - "$pipeline_json" <<'PYPIPELINEID'
import json
import re
import sys
import uuid

try:
    pipeline = json.loads(sys.argv[1])
except json.JSONDecodeError as exc:
    raise SystemExit("pipeline creation did not return valid JSON") from exc

if not isinstance(pipeline, dict):
    raise SystemExit("pipeline creation did not return a JSON object")
pipeline_id = pipeline.get("id")
slug = pipeline.get("slug")
if not isinstance(pipeline_id, str):
    raise SystemExit("pipeline creation did not return a top-level id")
try:
    uuid.UUID(pipeline_id)
except ValueError as exc:
    raise SystemExit("pipeline id is not a UUID") from exc
if not isinstance(slug, str) or re.fullmatch(r"[a-z0-9][a-z0-9-]{0,99}", slug) is None:
    raise SystemExit("pipeline creation returned an unsafe slug")
print(pipeline_id, slug)
PYPIPELINEID
)

created_pipeline="$(pipeline_snapshot "$pipeline_slug")"
created_builds="$(build_snapshot "$pipeline_slug")"
created_agents="$(agent_snapshot)"
verify_pipeline_state \
  "quarantine-unsigned" \
  "$pipeline_slug" \
  "$created_pipeline" \
  "$created_builds" \
  "$created_agents" >/dev/null

webhook_state="$(
  python3 - "$created_pipeline" <<'PYWEBHOOKSTATE'
import json
import sys
pipeline = json.loads(sys.argv[1])
provider = pipeline.get("provider") if isinstance(pipeline, dict) else None
value = provider.get("webhook_url") if isinstance(provider, dict) else None
print("present" if isinstance(value, str) and value else "absent")
PYWEBHOOKSTATE
)"

trap - ERR

cat <<EOF
HostPanel Buildkite control plane created in GitHub-trigger quarantine.

organization=$org
cluster_uuid=$cluster_uuid
hostpanel_upload_queue_uuid=$upload_queue_uuid
hostpanel_ci_queue_uuid=$ci_queue_uuid
hostpanel_qemu_queue_uuid=$qemu_queue_uuid
pipeline_uuid=$pipeline_uuid
pipeline_slug=$pipeline_slug
trigger_mode=none
webhook_state=$webhook_state

MANDATORY STOP:
No GitHub activity can create a build while trigger_mode=none. Do not connect an
agent and do not activate GitHub triggers yet. Statically sign Pipeline Settings,
add only the public checkout deploy key to GitHub, then run:

  $0 --org '$org' --enable-webhook '$pipeline_slug' --apply \
    --confirm-static-bootstrap-signed --confirm-public-deploy-key-added
EOF
