#!/usr/bin/env bash
set -euo pipefail

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
unset BASH_ENV ENV PYTHONPATH PYTHONHOME LD_PRELOAD LD_LIBRARY_PATH
umask 077

fail() {
  printf 'HostPanel Buildkite control-plane bootstrap failed: %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Usage:
  bootstrap-control-plane.sh --org ORGANIZATION [--apply --confirm-create]
  bootstrap-control-plane.sh --org ORGANIZATION --enable-webhook PIPELINE_SLUG [--apply --confirm-static-bootstrap-signed --confirm-public-deploy-key-added]

Default behavior is plan-only and performs no Buildkite API writes.
Creation mode creates the isolated cluster, three queues, and a pipeline containing
the reviewed static no-checkout bootstrap plus explicit GitHub provider policy.
It deliberately does not create a GitHub webhook.
Webhook mode is a separate activation phase and requires explicit confirmation
that Pipeline Settings have been statically signed and the public checkout deploy
key has been added to GitHub.
The tool never creates agent tokens, starts agents, prints API tokens, or changes
branch protection.
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
No GitHub webhook is created in this phase.

1. Switch to the reviewed Buildkite organization:
   bk auth switch '$org'
   bk auth status -o json

2. Verify there is no existing HostPanel cluster or pipeline for this repository:
   bk cluster list -o json
   bk pipeline list --repository '$repository' -o json

3. Create the isolated cluster and exactly these queues:
   $queue_upload
   $queue_ci
   $queue_qemu

4. Verify the cluster has no default queue and exactly those three queue keys.

5. Create the pipeline through the Buildkite REST API with:
   - exact repository '$repository'
   - the reviewed static no-checkout bootstrap
   - PR builds enabled
   - fork PR builds disabled
   - commit-status publishing enabled
   - /bk issue-comment retrigger enabled
   - exact-head PR builds (merge-commit builds disabled)
   - no webhook yet

STOP: generate signing/verification JWKS, statically sign Pipeline Settings, and
add only the public checkout deploy key to GitHub. Then use --enable-webhook in
a separate invocation. Do not connect any agent before the signed-bootstrap
checkpoint is complete.
EOF
}

print_webhook_plan() {
  cat <<EOF
HostPanel Buildkite webhook activation plan
organization: $org
pipeline:     $enable_webhook_slug
repository:   $repository

No agents are started by this tool.

Preconditions:
- Pipeline Settings contain the reviewed static no-checkout bootstrap.
- Pipeline Settings have been statically signed with the reviewed signing key.
- The GitHub provider policy still matches the reviewed fail-closed settings.
- The public half of the read-only checkout deploy key has been added to GitHub.
- No build has ever been created for this pipeline.
- No agent is connected to this HostPanel cluster.

Apply mode verifies those conditions, creates the standard GitHub App webhook
with POST /pipelines/<slug>/webhook, then verifies the pipeline exposes the
Buildkite webhook URL. It does not require the optional expanded-webhook-triggers
API and stops before any worker provisioning.
EOF
}

if [[ "$apply" != "true" ]]; then
  if [[ -n "$enable_webhook_slug" ]]; then
    print_webhook_plan
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
PY
}

if [[ -n "$enable_webhook_slug" ]]; then
  pipeline_json="$(bk pipeline view "$org/$enable_webhook_slug" -o json)"
  build_list="$(bk build list --pipeline "$org/$enable_webhook_slug" --limit 1 -o json)"
  agent_list="$(bk agent list --limit 1000 -o json)"

  python3 - \
    "$enable_webhook_slug" \
    "$repository" \
    "$pipeline_json" \
    "$build_list" \
    "$agent_list" <<'PY'
import base64
import json
import sys
import yaml

slug, repository, pipeline_raw, builds_raw, agents_raw = sys.argv[1:]

def load(label, raw):
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{label} did not return valid JSON") from exc

pipeline = load("bk pipeline view", pipeline_raw)
if not isinstance(pipeline, dict):
    raise SystemExit("bk pipeline view did not return a JSON object")
if pipeline.get("slug") != slug:
    raise SystemExit("pipeline slug does not match the requested webhook target")
if pipeline.get("repository") != repository:
    raise SystemExit("pipeline repository is not the reviewed HostPanel repository")
cluster_id = pipeline.get("cluster_id")
if not isinstance(cluster_id, str) or not cluster_id:
    raise SystemExit("pipeline is not assigned to the reviewed isolated cluster")

expected_provider = {
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
provider = pipeline.get("provider")
if not isinstance(provider, dict) or provider.get("id") != "github":
    raise SystemExit("pipeline is not using the GitHub provider")
settings = provider.get("settings")
if not isinstance(settings, dict):
    raise SystemExit("pipeline GitHub provider settings are unavailable")
for key, expected in expected_provider.items():
    if settings.get(key) != expected:
        raise SystemExit(f"pipeline GitHub provider setting mismatch: {key}")

configuration = pipeline.get("configuration")
if not isinstance(configuration, str):
    raise SystemExit("pipeline configuration is unavailable")
try:
    config = yaml.safe_load(configuration)
except yaml.YAMLError as exc:
    raise SystemExit("pipeline configuration is not valid YAML") from exc
if not isinstance(config, dict) or set(config) != {"steps"}:
    raise SystemExit("pipeline configuration must contain only steps")
steps = config.get("steps")
if not isinstance(steps, list) or len(steps) != 1 or not isinstance(steps[0], dict):
    raise SystemExit("pipeline configuration must contain exactly one static bootstrap step")
step = steps[0]
allowed = {"label", "key", "command", "checkout", "agents", "signature"}
if set(step) - allowed:
    raise SystemExit("static bootstrap contains unreviewed step fields")
if step.get("label") != ":pipeline: Upload reviewed pipeline":
    raise SystemExit("static bootstrap label mismatch")
if step.get("key") != "upload-reviewed-pipeline":
    raise SystemExit("static bootstrap key mismatch")
if step.get("command") != "/usr/local/libexec/hostpanel-upload-pipeline":
    raise SystemExit("static bootstrap command mismatch")
if step.get("checkout") != {"skip": True}:
    raise SystemExit("static bootstrap checkout policy mismatch")
if step.get("agents") != {"queue": "hostpanel-upload"}:
    raise SystemExit("static bootstrap queue mismatch")

signature = step.get("signature")
if not isinstance(signature, dict):
    raise SystemExit("static bootstrap is not signed")
if set(signature) != {"algorithm", "signed_fields", "value"}:
    raise SystemExit("static bootstrap signature shape is unexpected")
if signature.get("algorithm") != "EdDSA":
    raise SystemExit("static bootstrap signature algorithm is not EdDSA")
signed_fields = signature.get("signed_fields")
if not isinstance(signed_fields, list) or not {"command", "repository_url"}.issubset(set(signed_fields)):
    raise SystemExit("static bootstrap signature does not cover command and repository")
value = signature.get("value")
if not isinstance(value, str) or value.count(".") != 2 or value.split(".")[1] != "":
    raise SystemExit("static bootstrap signature value is not a detached compact JWS")

try:
    encoded_header = value.split(".")[0]
    encoded_header += "=" * (-len(encoded_header) % 4)
    header = json.loads(base64.urlsafe_b64decode(encoded_header).decode("utf-8"))
except Exception as exc:
    raise SystemExit("static bootstrap signature header is invalid") from exc
if not isinstance(header, dict) or header.get("alg") != "EdDSA":
    raise SystemExit("static bootstrap JWS header algorithm mismatch")
if header.get("kid") != "hostpanel-2026-08":
    raise SystemExit("static bootstrap JWS key ID mismatch")

builds = load("bk build list", builds_raw)
if isinstance(builds, list):
    count = len(builds)
elif isinstance(builds, dict) and isinstance(builds.get("builds"), list):
    count = len(builds["builds"])
else:
    raise SystemExit("bk build list returned an unrecognized JSON shape")
if count != 0:
    raise SystemExit("pipeline already has build history; inspect it before creating the webhook")

agents = load("bk agent list", agents_raw)
if isinstance(agents, list):
    agent_items = agents
elif isinstance(agents, dict) and isinstance(agents.get("agents"), list):
    agent_items = agents["agents"]
else:
    raise SystemExit("bk agent list returned an unrecognized JSON shape")
if len(agent_items) >= 1000:
    raise SystemExit("agent inventory reached the query limit; cannot prove the target cluster is empty")
for agent in agent_items:
    if not isinstance(agent, dict):
        raise SystemExit("bk agent list contained a non-object agent")
    web_url = agent.get("web_url")
    if isinstance(web_url, str) and f"/clusters/{cluster_id}/" in web_url:
        raise SystemExit("an agent is already connected to the HostPanel cluster")
PY

  bk api --method POST "/pipelines/$enable_webhook_slug/webhook" >/dev/null

  post_webhook_pipeline="$(bk pipeline view "$org/$enable_webhook_slug" -o json)"
  python3 - "$repository" "$post_webhook_pipeline" <<'PY'
import json
import sys

repository, raw = sys.argv[1:]
try:
    pipeline = json.loads(raw)
except json.JSONDecodeError as exc:
    raise SystemExit("post-webhook pipeline view did not return valid JSON") from exc
if not isinstance(pipeline, dict) or pipeline.get("repository") != repository:
    raise SystemExit("post-webhook pipeline identity mismatch")
provider = pipeline.get("provider")
if not isinstance(provider, dict) or provider.get("id") != "github":
    raise SystemExit("post-webhook pipeline is not using the GitHub provider")
webhook_url = provider.get("webhook_url")
if not isinstance(webhook_url, str) or not webhook_url.startswith("https://webhook.buildkite.com/deliver/"):
    raise SystemExit("Buildkite did not expose the expected GitHub webhook URL")
expected = {
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
settings = provider.get("settings")
if not isinstance(settings, dict):
    raise SystemExit("post-webhook GitHub provider settings are unavailable")
for key, value in expected.items():
    if settings.get(key) != value:
        raise SystemExit(f"post-webhook GitHub provider setting mismatch: {key}")
PY

  cat <<EOF
HostPanel Buildkite GitHub webhook created and verified.

organization=$org
pipeline_slug=$enable_webhook_slug
repository=$repository

No agents were started. Provision only reviewed disposable workers after the
signed bootstrap, deploy-key, registration-token, and smoke-test requirements
in BUILDKITE.md are satisfied.
EOF
  exit 0
fi

cluster_list="$(bk cluster list -o json)"
pipeline_list="$(bk pipeline list --repository "$repository" -o json)"

python3 - "$cluster_name" "$repository" "$cluster_list" "$pipeline_list" <<'PY'
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
        raise SystemExit("an existing HostPanel cluster was found; inspect it instead of creating a duplicate")

for item in dicts(pipelines):
    name = item.get("name")
    if isinstance(name, str) and name.casefold() == cluster_name.casefold():
        raise SystemExit("an existing HostPanel pipeline name was found; inspect it instead of creating a duplicate")
    if item.get("repository") == repository or item.get("repository_url") == repository:
        raise SystemExit("an existing pipeline for the HostPanel repository was found; inspect it instead of creating a duplicate")
PY

cluster_uuid=""
pipeline_uuid=""
pipeline_slug=""
upload_queue_uuid=""
ci_queue_uuid=""
qemu_queue_uuid=""

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
queue_list="$(bk queue list "$cluster_uuid" -o json)"
python3 - "$cluster_uuid" "$cluster_view" "$queue_list" <<'PY'
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
PY

pipeline_payload="$(
  python3 - "$pipeline_name" "$cluster_uuid" "$repository" "$static_bootstrap" <<'PY'
import json
import sys
name, cluster_id, repository, configuration = sys.argv[1:]
print(json.dumps({
    "name": name,
    "cluster_id": cluster_id,
    "repository": repository,
    "configuration": configuration,
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
    },
}))
PY
)"
pipeline_json="$(bk api --method POST /pipelines --data "$pipeline_payload")"

read -r pipeline_uuid pipeline_slug < <(
  python3 - "$cluster_uuid" "$repository" "$static_bootstrap" "$pipeline_json" <<'PY'
import json
import re
import sys
import uuid
import yaml

cluster_id, repository, expected_configuration, raw = sys.argv[1:]
try:
    pipeline = json.loads(raw)
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
if pipeline.get("repository") != repository:
    raise SystemExit("created pipeline repository mismatch")
if pipeline.get("cluster_id") != cluster_id:
    raise SystemExit("created pipeline cluster mismatch")
configuration = pipeline.get("configuration")
if not isinstance(configuration, str):
    raise SystemExit("created pipeline configuration is unavailable")
try:
    actual = yaml.safe_load(configuration)
    expected = yaml.safe_load(expected_configuration)
except yaml.YAMLError as exc:
    raise SystemExit("created pipeline configuration is not valid YAML") from exc
if actual != expected:
    raise SystemExit("created pipeline configuration is not the reviewed static bootstrap")

expected_provider = {
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
provider = pipeline.get("provider")
if not isinstance(provider, dict) or provider.get("id") != "github":
    raise SystemExit("created pipeline is not using the GitHub provider")
settings = provider.get("settings")
if not isinstance(settings, dict):
    raise SystemExit("created pipeline GitHub provider settings are unavailable")
for key, value in expected_provider.items():
    if settings.get(key) != value:
        raise SystemExit(f"created pipeline GitHub provider setting mismatch: {key}")

webhook_url = provider.get("webhook_url")
if isinstance(webhook_url, str) and webhook_url:
    raise SystemExit("created pipeline unexpectedly exposes a webhook before activation")
print(pipeline_id, slug)
PY
)

trap - ERR

cat <<EOF
HostPanel Buildkite control plane created without a GitHub webhook.

organization=$org
cluster_uuid=$cluster_uuid
hostpanel_upload_queue_uuid=$upload_queue_uuid
hostpanel_ci_queue_uuid=$ci_queue_uuid
hostpanel_qemu_queue_uuid=$qemu_queue_uuid
pipeline_uuid=$pipeline_uuid
pipeline_slug=$pipeline_slug

MANDATORY STOP:
Do not connect an agent and do not enable the GitHub webhook yet. Generate the
signing/verification JWKS, statically sign Pipeline Settings, and add only the
public checkout deploy key to GitHub. After that checkpoint, run:

  $0 --org '$org' --enable-webhook '$pipeline_slug' --apply --confirm-static-bootstrap-signed --confirm-public-deploy-key-added
EOF
