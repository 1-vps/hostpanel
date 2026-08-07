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
- proves the active Buildkite token has the required scopes before any write;
- refuses duplicate HostPanel control-plane resources;
- creates one isolated cluster and exactly three queues;
- creates the pipeline with the reviewed static no-checkout bootstrap;
- keeps all GitHub triggers quarantined with trigger_mode=none;
- verifies zero build history and zero connected/stopping agents on every HostPanel queue.

Activation mode:
- requires explicit confirmation that Pipeline Settings were statically signed and
  that the public read-only checkout deploy key is installed in GitHub;
- reverifies the signed static bootstrap while still quarantined;
- reuses an existing valid Buildkite webhook or creates one if absent;
- activates the reviewed GitHub trigger policy only with an explicit pipeline PATCH;
- reverifies the active policy, webhook, zero build history, and zero
  connected/stopping agents on all HostPanel queues.

The tool never creates agent tokens, starts agents, prints API tokens, changes
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
  [[ "$enable_webhook_slug" =~ ^[a-z0-9][a-z0-9-]{0,99}$ ]] || fail "invalid --enable-webhook slug"
  [[ "$enable_webhook_slug" == "hostpanel" ]] || fail "--enable-webhook must target the reviewed hostpanel pipeline slug"
  [[ "$confirm_create" == "false" ]] || fail "--confirm-create is not valid with --enable-webhook"
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
pipeline_slug_expected='hostpanel'
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

read -r -d '' quarantine_provider_json <<'JSON' || true
{
  "filter_enabled": false,
  "build_branches": false,
  "build_pull_requests": false,
  "build_pull_request_forks": false,
  "build_tags": false,
  "ignore_default_branch_pull_requests": false,
  "publish_commit_status": false,
  "publish_commit_status_per_step": false,
  "pull_request_branch_filter_enabled": false,
  "skip_pull_request_builds_for_existing_commits": false,
  "build_pull_request_base_branch_changed": false,
  "build_pull_request_labels_changed": false,
  "build_pull_request_ready_for_review": false,
  "build_pull_request_reopened": false,
  "build_pull_request_edited": false,
  "build_pull_request_converted_to_draft": false,
  "build_pull_request_review_requested": false,
  "build_check_run_completed": false,
  "build_pull_request_review_submitted": false,
  "build_pull_request_review_dismissed": false,
  "build_release_published": false,
  "build_release_created": false,
  "build_release_released": false,
  "build_issue_comment_created": false,
  "build_deployment_status_created": false,
  "build_pull_request_review_comment_created": false,
  "build_pull_request_dequeued": false,
  "build_create_event": false,
  "cancel_deleted_branch_builds": false,
  "build_merge_group_checks_requested": false,
  "cancel_when_merge_group_destroyed": false,
  "use_merge_group_base_commit_for_git_diff_base": false,
  "prefix_pull_request_fork_branch_names": true,
  "publish_blocked_as_pending": true,
  "separate_pull_request_statuses": true,
  "build_pull_request_merge_commits": false,
  "skip_builds_for_existing_commits": false,
  "skip_builds_for_closed_pull_requests": true,
  "use_step_key_as_commit_status": false,
  "trigger_mode": "none"
}
JSON

read -r -d '' active_provider_json <<'JSON' || true
{
  "filter_enabled": false,
  "build_branches": true,
  "build_pull_requests": true,
  "build_pull_request_forks": false,
  "build_tags": false,
  "ignore_default_branch_pull_requests": false,
  "publish_commit_status": true,
  "publish_commit_status_per_step": false,
  "pull_request_branch_filter_enabled": false,
  "skip_pull_request_builds_for_existing_commits": false,
  "build_pull_request_base_branch_changed": false,
  "build_pull_request_labels_changed": false,
  "build_pull_request_ready_for_review": true,
  "build_pull_request_reopened": false,
  "build_pull_request_edited": false,
  "build_pull_request_converted_to_draft": false,
  "build_pull_request_review_requested": false,
  "build_check_run_completed": false,
  "build_pull_request_review_submitted": false,
  "build_pull_request_review_dismissed": false,
  "build_release_published": false,
  "build_release_created": false,
  "build_release_released": false,
  "build_issue_comment_created": true,
  "issue_comment_command_word": "/bk",
  "issue_comment_match_mode": "exact",
  "build_deployment_status_created": false,
  "build_pull_request_review_comment_created": false,
  "build_pull_request_dequeued": false,
  "build_create_event": false,
  "cancel_deleted_branch_builds": false,
  "build_merge_group_checks_requested": false,
  "cancel_when_merge_group_destroyed": false,
  "use_merge_group_base_commit_for_git_diff_base": false,
  "prefix_pull_request_fork_branch_names": true,
  "publish_blocked_as_pending": true,
  "separate_pull_request_statuses": true,
  "build_pull_request_merge_commits": false,
  "skip_builds_for_existing_commits": false,
  "skip_builds_for_closed_pull_requests": true,
  "use_step_key_as_commit_status": false,
  "trigger_mode": "code"
}
JSON

print_create_plan() {
  cat <<EOF
HostPanel Buildkite control-plane creation plan
organization: $org
repository:   $repository

No agents are started by this tool.

1. Switch to the reviewed Buildkite organization and prove token scopes:
   bk auth switch '$org'
   bk auth status -o json
   bk api /access-token

2. Refuse existing HostPanel cluster/pipeline or an existing pipeline for:
   $repository

3. Create one cluster with exactly these queue keys:
   $queue_upload
   $queue_ci
   $queue_qemu

4. Prove each queue has zero connected/stopping agents using the Buildkite Agents
   REST API filtered by cluster_queue_id.

5. Create the pipeline with the reviewed static no-checkout bootstrap and
   trigger_mode=none quarantine. GitHub activity must not create builds.

6. Reverify exact configuration, quarantine policy, zero build history, and
   zero connected/stopping agents on all three queues.

MANDATORY STOP:
Sign Pipeline Settings from the reviewed merged bootstrap and install only the
public read-only checkout deploy key in GitHub. Then run the separate activation
mode. Do not connect an agent before activation verification completes.
EOF
}

print_activation_plan() {
  cat <<EOF
HostPanel Buildkite activation plan
organization: $org
pipeline:     $enable_webhook_slug
repository:   $repository

No agents are started by this tool.

Preconditions:
- Pipeline Settings contain the reviewed static no-checkout bootstrap.
- Pipeline Settings are statically signed with key id hostpanel-2026-08.
- GitHub provider policy is still trigger_mode=none quarantine.
- The public half of the read-only checkout deploy key is installed in GitHub.
- Build history is empty.
- Every HostPanel queue has zero connected/stopping agents.

Apply mode reverifies those conditions, reuses a valid existing Buildkite
webhook or creates one if absent, then PATCHes only provider_settings to the
reviewed trigger_mode=code policy and reverifies the result.

No worker provisioning is performed.
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

token_json="$(bk api /access-token)"
python3 - "$token_json" <<'PY'
import json
import sys

try:
    payload = json.loads(sys.argv[1])
except json.JSONDecodeError as exc:
    raise SystemExit("bk api /access-token did not return valid JSON") from exc
if not isinstance(payload, dict):
    raise SystemExit("bk api /access-token did not return a JSON object")
scopes = payload.get("scopes")
if not isinstance(scopes, list) or not all(isinstance(item, str) for item in scopes):
    raise SystemExit("Buildkite access-token scopes are unavailable")
required = {
    "read_clusters",
    "write_clusters",
    "read_pipelines",
    "write_pipelines",
    "read_builds",
    "read_agents",
}
missing = sorted(required.difference(scopes))
if missing:
    raise SystemExit("Buildkite token is missing required scopes: " + ", ".join(missing))
PY

top_uuid() {
  local raw="$1"
  local label="$2"
  python3 - "$label" "$raw" <<'PY'
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
value = payload.get("id") or payload.get("uuid")
if not isinstance(value, str) or not value:
    raise SystemExit(f"{label} did not contain a top-level id/uuid")
try:
    uuid.UUID(value)
except ValueError as exc:
    raise SystemExit(f"{label} id is not a UUID") from exc
print(value)
PY
}

queue_map_from_json() {
  local cluster_id="$1"
  local raw="$2"
  python3 - "$cluster_id" "$raw" <<'PY'
import json
import sys
import uuid

cluster_id, raw = sys.argv[1:]
try:
    payload = json.loads(raw)
except json.JSONDecodeError as exc:
    raise SystemExit("bk queue list did not return valid JSON") from exc
if isinstance(payload, dict) and isinstance(payload.get("queues"), list):
    payload = payload["queues"]
if not isinstance(payload, list):
    raise SystemExit("bk queue list returned an unrecognized JSON shape")
if len(payload) >= 100:
    raise SystemExit("HostPanel queue inventory reached the 100-item bound; cannot prove completeness")

expected = {"hostpanel-upload", "hostpanel-ci", "hostpanel-qemu"}
mapping = {}
for item in payload:
    if not isinstance(item, dict):
        raise SystemExit("bk queue list contained a non-object queue")
    key = item.get("key")
    queue_id = item.get("id") or item.get("uuid")
    if not isinstance(key, str) or not isinstance(queue_id, str):
        raise SystemExit("bk queue list contained an invalid queue")
    if item.get("hosted") is not False and item.get("hosted") is not None:
        raise SystemExit(f"HostPanel queue {key} has an unexpected hosted flag")
    if item.get("hosted_agents") is not None:
        raise SystemExit(f"HostPanel queue {key} is Buildkite-hosted; self-hosted queues are required")
    if item.get("dispatch_paused") is not False:
        raise SystemExit(f"HostPanel queue {key} must explicitly have dispatch_paused=false")
    try:
        uuid.UUID(queue_id)
    except ValueError as exc:
        raise SystemExit("Buildkite queue id is not a UUID") from exc
    mapping[key] = queue_id

if len(payload) != 3 or set(mapping) != expected:
    raise SystemExit("HostPanel cluster does not contain exactly the three reviewed queue objects")
for key in ("hostpanel-upload", "hostpanel-ci", "hostpanel-qemu"):
    print(f"{key}={mapping[key]}")
PY
}

assert_queue_has_no_connected_agents() {
  local queue_uuid="$1"
  local queue_key="$2"
  [[ "$queue_uuid" =~ ^[0-9a-fA-F-]{36}$ ]] || fail "unsafe queue UUID for $queue_key"
  local raw
  raw="$(bk api "/organizations/$org/agents?cluster_queue_id=$queue_uuid")"
  python3 - "$queue_key" "$raw" <<'PY'
import json
import sys

queue_key, raw = sys.argv[1:]
try:
    payload = json.loads(raw)
except json.JSONDecodeError as exc:
    raise SystemExit(f"agent query for {queue_key} did not return valid JSON") from exc
if not isinstance(payload, list):
    raise SystemExit(f"agent query for {queue_key} did not return a JSON list")
if payload:
    raise SystemExit(
        f"HostPanel queue {queue_key} already has connected/stopping agents; "
        "do not activate the control plane"
    )
PY
}

load_queue_ids() {
  local cluster_uuid="$1"
  local queue_json
  queue_json="$(bk queue list "$cluster_uuid" --limit 100 --per-page 100 -o json)"
  local mapping
  mapping="$(queue_map_from_json "$cluster_uuid" "$queue_json")"
  upload_queue_uuid="$(printf '%s\n' "$mapping" | awk -F= '$1=="hostpanel-upload"{print $2}')"
  ci_queue_uuid="$(printf '%s\n' "$mapping" | awk -F= '$1=="hostpanel-ci"{print $2}')"
  qemu_queue_uuid="$(printf '%s\n' "$mapping" | awk -F= '$1=="hostpanel-qemu"{print $2}')"
  [[ -n "$upload_queue_uuid" && -n "$ci_queue_uuid" && -n "$qemu_queue_uuid" ]] || {
    fail "failed to resolve all HostPanel queue UUIDs"
  }
}

assert_no_target_agents() {
  assert_queue_has_no_connected_agents "$upload_queue_uuid" "$queue_upload"
  assert_queue_has_no_connected_agents "$ci_queue_uuid" "$queue_ci"
  assert_queue_has_no_connected_agents "$qemu_queue_uuid" "$queue_qemu"
}

assert_zero_builds() {
  local slug="$1"
  local raw
  raw="$(bk build list --pipeline "$org/$slug" --limit 1 -o json)"
  python3 - "$raw" <<'PY'
import json
import sys

try:
    payload = json.loads(sys.argv[1])
except json.JSONDecodeError as exc:
    raise SystemExit("bk build list did not return valid JSON") from exc
if isinstance(payload, dict) and isinstance(payload.get("builds"), list):
    payload = payload["builds"]
if not isinstance(payload, list):
    raise SystemExit("bk build list returned an unrecognized JSON shape")
if payload:
    raise SystemExit("pipeline already has build history; inspect it before activation")
PY
}

verify_pipeline() {
  local mode="$1"
  local slug="$2"
  local raw="$3"
  local require_signature="$4"
  local require_webhook="$5"
  local expected_provider="$quarantine_provider_json"
  [[ "$mode" == "active" ]] && expected_provider="$active_provider_json"

  python3 - \
    "$mode" \
    "$slug" \
    "$pipeline_name" \
    "$repository" \
    "$static_bootstrap" \
    "$expected_provider" \
    "$require_signature" \
    "$require_webhook" \
    "$raw" <<'PY'
import base64
import json
import re
import sys
import uuid
import yaml

mode, slug, expected_name, repository, expected_configuration, expected_provider_raw, require_signature, require_webhook, raw = sys.argv[1:]

try:
    pipeline = json.loads(raw)
except json.JSONDecodeError as exc:
    raise SystemExit("pipeline view did not return valid JSON") from exc
if not isinstance(pipeline, dict):
    raise SystemExit("pipeline view did not return a JSON object")
if pipeline.get("slug") != slug or slug != "hostpanel":
    raise SystemExit("pipeline slug mismatch")
if pipeline.get("name") != expected_name:
    raise SystemExit("pipeline name mismatch")
if pipeline.get("repository") != repository:
    raise SystemExit("pipeline repository mismatch")
if pipeline.get("visibility") != "private":
    raise SystemExit("pipeline visibility must remain private")
if pipeline.get("env") not in ({}, None):
    raise SystemExit("pipeline-level environment must be empty")
if pipeline.get("branch_configuration") != "main":
    raise SystemExit("pipeline branch configuration must restrict branch builds to main")
if pipeline.get("default_branch") != "main":
    raise SystemExit("pipeline default branch must be main")
if pipeline.get("allow_rebuilds") is not False:
    raise SystemExit("pipeline rebuilds must be disabled")

cluster_id = pipeline.get("cluster_id")
if not isinstance(cluster_id, str):
    raise SystemExit("pipeline cluster id is unavailable")
try:
    uuid.UUID(cluster_id)
except ValueError as exc:
    raise SystemExit("pipeline cluster id is not a UUID") from exc

configuration = pipeline.get("configuration")
if not isinstance(configuration, str):
    raise SystemExit("pipeline configuration is unavailable")
try:
    actual = yaml.safe_load(configuration)
    expected = yaml.safe_load(expected_configuration)
except yaml.YAMLError as exc:
    raise SystemExit("pipeline configuration is not valid YAML") from exc

if not isinstance(actual, dict) or set(actual) != {"steps"}:
    raise SystemExit("pipeline configuration must contain only steps")
steps = actual.get("steps")
if not isinstance(steps, list) or len(steps) != 1 or not isinstance(steps[0], dict):
    raise SystemExit("pipeline configuration must contain exactly one bootstrap step")

expected_step = expected["steps"][0]
step = steps[0]
allowed = {"label", "key", "command", "checkout", "agents", "signature"}
if set(step) - allowed:
    raise SystemExit("static bootstrap contains unreviewed step fields")
for key in ("label", "key", "command", "checkout", "agents"):
    if step.get(key) != expected_step.get(key):
        raise SystemExit(f"static bootstrap {key} mismatch")

provider = pipeline.get("provider")
if not isinstance(provider, dict) or provider.get("id") != "github":
    raise SystemExit("pipeline is not using the GitHub provider")
settings = provider.get("settings")
if not isinstance(settings, dict):
    raise SystemExit("pipeline GitHub provider settings are unavailable")
try:
    expected_provider = json.loads(expected_provider_raw)
except json.JSONDecodeError as exc:
    raise SystemExit("internal expected provider policy is invalid JSON") from exc
for key, expected_value in expected_provider.items():
    if settings.get(key) != expected_value:
        raise SystemExit(f"{mode} provider setting mismatch: {key}")
allowed_true_settings = {
    key for key, value in expected_provider.items()
    if value is True
}
for key, value in settings.items():
    if isinstance(value, bool) and value and key not in allowed_true_settings:
        raise SystemExit(f"{mode} provider has an unreviewed enabled boolean setting: {key}")

webhook_url = provider.get("webhook_url")
if require_webhook == "true":
    if not isinstance(webhook_url, str) or not webhook_url.startswith(
        "https://webhook.buildkite.com/deliver/"
    ):
        raise SystemExit("Buildkite webhook URL is missing or unexpected")
elif webhook_url is not None and webhook_url != "":
    if not isinstance(webhook_url, str) or not webhook_url.startswith(
        "https://webhook.buildkite.com/deliver/"
    ):
        raise SystemExit("unexpected non-Buildkite webhook URL")

signature = step.get("signature")
if require_signature == "true":
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
    if not isinstance(value, str) or value.count(".") != 2 or value.split(".")[1] != "":
        raise SystemExit("static bootstrap signature value is not detached compact JWS")
    try:
        encoded = value.split(".")[0]
        encoded += "=" * (-len(encoded) % 4)
        header = json.loads(base64.urlsafe_b64decode(encoded).decode("utf-8"))
    except Exception as exc:
        raise SystemExit("static bootstrap JWS header is invalid") from exc
    if not isinstance(header, dict) or header.get("alg") != "EdDSA":
        raise SystemExit("static bootstrap JWS algorithm mismatch")
    if header.get("kid") != "hostpanel-2026-08":
        raise SystemExit("static bootstrap JWS key ID mismatch")
else:
    if signature is not None:
        raise SystemExit("newly-created quarantine bootstrap unexpectedly contains a signature")

print(cluster_id)
PY
}

if [[ -n "$enable_webhook_slug" ]]; then
  pipeline_json="$(bk pipeline view "$org/$enable_webhook_slug" -o json)"
  cluster_uuid="$(verify_pipeline quarantine "$enable_webhook_slug" "$pipeline_json" true false)"
  cluster_view="$(bk cluster view "$cluster_uuid" -o json)"
  python3 - "$cluster_uuid" "$cluster_view" <<'PY'
import json
import sys
cluster_id, raw = sys.argv[1:]
try:
    payload = json.loads(raw)
except json.JSONDecodeError as exc:
    raise SystemExit("bk cluster view did not return valid JSON") from exc
if not isinstance(payload, dict) or payload.get("id") != cluster_id:
    raise SystemExit("pipeline cluster identity mismatch")
if "default_queue_id" not in payload or payload["default_queue_id"] is not None:
    raise SystemExit("HostPanel cluster must explicitly report default_queue_id=null")
PY
  load_queue_ids "$cluster_uuid"
  assert_no_target_agents
  assert_zero_builds "$enable_webhook_slug"

  webhook_url="$(
    python3 - "$pipeline_json" <<'PY'
import json
import sys
payload = json.loads(sys.argv[1])
provider = payload.get("provider")
value = provider.get("webhook_url") if isinstance(provider, dict) else None
print(value if isinstance(value, str) else "")
PY
  )"

  if [[ -z "$webhook_url" ]]; then
    bk api --method POST "/pipelines/$enable_webhook_slug/webhook" >/dev/null
  fi

  pipeline_json="$(bk pipeline view "$org/$enable_webhook_slug" -o json)"
  verify_pipeline quarantine "$enable_webhook_slug" "$pipeline_json" true true >/dev/null
  assert_no_target_agents
  assert_zero_builds "$enable_webhook_slug"

  active_patch="$(
    python3 - "$active_provider_json" <<'PY'
import json
import sys
print(json.dumps({"provider_settings": json.loads(sys.argv[1])}))
PY
  )"
  bk api --method PATCH "/pipelines/$enable_webhook_slug" --data "$active_patch" >/dev/null

  pipeline_json="$(bk pipeline view "$org/$enable_webhook_slug" -o json)"
  verify_pipeline active "$enable_webhook_slug" "$pipeline_json" true true >/dev/null
  assert_no_target_agents
  assert_zero_builds "$enable_webhook_slug"

  cat <<EOF
HostPanel Buildkite pipeline activation verified.

organization=$org
pipeline_slug=$enable_webhook_slug
repository=$repository
provider_trigger_mode=code

No agents were started. Provision only reviewed disposable workers after all
signing, deploy-key, registration-token, and smoke-test requirements in
BUILDKITE.md are satisfied.
EOF
  exit 0
fi

cluster_list="$(bk api "/clusters?per_page=100")"
pipeline_list="$(bk pipeline list --limit 3000 -o json)"

python3 - "$cluster_name" "$pipeline_name" "$pipeline_slug_expected" "$repository" "$cluster_list" "$pipeline_list" <<'PY'
import json
import sys

cluster_name, pipeline_name, pipeline_slug, repository, cluster_raw, pipeline_raw = sys.argv[1:]

def load(label, raw):
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{label} did not return valid JSON") from exc

def objects(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from objects(child)

clusters = load("bk api /clusters", cluster_raw)
pipelines = load("bk pipeline list", pipeline_raw)
if not isinstance(clusters, list):
    raise SystemExit("cluster inventory returned an unexpected JSON shape")
if len(clusters) >= 100:
    raise SystemExit("cluster inventory reached the 100-item pagination bound; cannot prove completeness")
if isinstance(pipelines, dict) and isinstance(pipelines.get("pipelines"), list):
    pipelines = pipelines["pipelines"]
if not isinstance(pipelines, list):
    raise SystemExit("pipeline inventory returned an unexpected JSON shape")
if len(pipelines) >= 3000:
    raise SystemExit("pipeline inventory reached the CLI pagination bound; cannot prove completeness")

for item in objects(clusters):
    name = item.get("name")
    if isinstance(name, str) and name.casefold() == cluster_name.casefold():
        raise SystemExit("an existing HostPanel cluster was found; inspect it instead of creating a duplicate")

for item in objects(pipelines):
    name = item.get("name")
    if isinstance(name, str) and name.casefold() == pipeline_name.casefold():
        raise SystemExit("an existing HostPanel pipeline name was found; inspect it instead of creating a duplicate")
    if item.get("slug") == pipeline_slug:
        raise SystemExit("an existing HostPanel pipeline slug was found; inspect it instead of creating a duplicate")
    if item.get("repository") == repository or item.get("repository_url") == repository:
        raise SystemExit("an existing pipeline for the HostPanel repository was found; inspect it instead of creating a duplicate")
PY

cluster_uuid=""
upload_queue_uuid=""
ci_queue_uuid=""
qemu_queue_uuid=""
pipeline_uuid=""
pipeline_slug=""

on_error() {
  rc=$?
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

cluster_json="$(bk cluster create \
  --name "$cluster_name" \
  --description 'Disposable hardened HostPanel CI' \
  -o json)"
cluster_uuid="$(top_uuid "$cluster_json" 'bk cluster create')"

upload_queue_json="$(bk queue create "$cluster_uuid" \
  --key "$queue_upload" \
  --description 'Trusted no-checkout pipeline signer/uploader' \
  -o json)"
upload_queue_uuid="$(top_uuid "$upload_queue_json" 'bk queue create hostpanel-upload')"

ci_queue_json="$(bk queue create "$cluster_uuid" \
  --key "$queue_ci" \
  --description 'Disposable repository CI workers' \
  -o json)"
ci_queue_uuid="$(top_uuid "$ci_queue_json" 'bk queue create hostpanel-ci')"

qemu_queue_json="$(bk queue create "$cluster_uuid" \
  --key "$queue_qemu" \
  --description 'Disposable KVM post-merge acceptance workers' \
  -o json)"
qemu_queue_uuid="$(top_uuid "$qemu_queue_json" 'bk queue create hostpanel-qemu')"

cluster_view="$(bk cluster view "$cluster_uuid" -o json)"
python3 - "$cluster_uuid" "$cluster_view" <<'PY'
import json
import sys
cluster_id, raw = sys.argv[1:]
try:
    payload = json.loads(raw)
except json.JSONDecodeError as exc:
    raise SystemExit("bk cluster view did not return valid JSON") from exc
if not isinstance(payload, dict) or payload.get("id") != cluster_id:
    raise SystemExit("created cluster identity mismatch")
if "default_queue_id" not in payload or payload["default_queue_id"] is not None:
    raise SystemExit("created HostPanel cluster must explicitly report default_queue_id=null")
PY

load_queue_ids "$cluster_uuid"
assert_no_target_agents

pipeline_payload="$(
  python3 - \
    "$pipeline_name" \
    "$pipeline_slug_expected" \
    "$cluster_uuid" \
    "$repository" \
    "$static_bootstrap" \
    "$quarantine_provider_json" <<'PY'
import json
import sys
name, slug, cluster_id, repository, configuration, provider_raw = sys.argv[1:]
print(json.dumps({
    "name": name,
    "slug": slug,
    "cluster_id": cluster_id,
    "repository": repository,
    "branch_configuration": "main",
    "default_branch": "main",
    "visibility": "private",
    "env": {},
    "allow_rebuilds": False,
    "configuration": configuration,
    "provider_settings": json.loads(provider_raw),
}))
PY
)"

pipeline_json="$(bk api --method POST /pipelines --data "$pipeline_payload")"
read -r pipeline_uuid pipeline_slug < <(
  python3 - "$pipeline_json" <<'PY'
import json
import re
import sys
import uuid
try:
    payload = json.loads(sys.argv[1])
except json.JSONDecodeError as exc:
    raise SystemExit("pipeline creation did not return valid JSON") from exc
if not isinstance(payload, dict):
    raise SystemExit("pipeline creation did not return a JSON object")
pipeline_id = payload.get("id")
slug = payload.get("slug")
if not isinstance(pipeline_id, str):
    raise SystemExit("pipeline creation did not return a top-level id")
try:
    uuid.UUID(pipeline_id)
except ValueError as exc:
    raise SystemExit("pipeline id is not a UUID") from exc
if slug != "hostpanel":
    raise SystemExit("pipeline creation did not return the reviewed hostpanel slug")
print(pipeline_id, slug)
PY
)

pipeline_json="$(bk pipeline view "$org/$pipeline_slug" -o json)"
created_cluster="$(verify_pipeline quarantine "$pipeline_slug" "$pipeline_json" false false)"
[[ "$created_cluster" == "$cluster_uuid" ]] || fail "created pipeline cluster mismatch"
assert_zero_builds "$pipeline_slug"
assert_no_target_agents

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
provider_trigger_mode=none

MANDATORY STOP:
Do not connect an agent and do not activate GitHub triggers yet. Generate the
signing/verification JWKS, statically sign Pipeline Settings, and install only
the public checkout deploy key in GitHub. After that checkpoint, run:

  $0 --org '$org' --enable-webhook '$pipeline_slug' --apply \
    --confirm-static-bootstrap-signed --confirm-public-deploy-key-added
EOF
