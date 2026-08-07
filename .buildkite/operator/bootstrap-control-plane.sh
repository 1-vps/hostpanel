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

Default behavior is plan-only and performs no Buildkite API writes.
--apply requires --confirm-create and an already authenticated bk CLI session.
The script creates only the Buildkite cluster, queues, pipeline, and GitHub webhook.
It never creates agent tokens, starts agents, prints API tokens, or changes branch protection.
EOF
}

org=""
apply=false
confirm_create=false

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

repository='git@github.com:1-vps/hostpanel.git'
cluster_name='HostPanel'
pipeline_name='HostPanel'
queue_upload='hostpanel-upload'
queue_ci='hostpanel-ci'
queue_qemu='hostpanel-qemu'

print_plan() {
  cat <<EOF
HostPanel Buildkite control-plane plan
organization: $org
repository:   $repository

No agents are started by this tool.

1. Switch to the reviewed Buildkite organization:
   bk auth switch '$org'
   bk auth status -o json

2. Verify there is no existing HostPanel cluster or pipeline for this repository:
   bk cluster list -o json
   bk pipeline list --repository '$repository' -o json

3. Create the isolated cluster:
   bk cluster create --name '$cluster_name' --description 'Disposable hardened HostPanel CI' -o json

4. Create these queues in the returned cluster UUID:
   bk queue create '<cluster-uuid>' --key '$queue_upload' --description 'Trusted no-checkout pipeline signer/uploader' -o json
   bk queue create '<cluster-uuid>' --key '$queue_ci' --description 'Disposable repository CI workers' -o json
   bk queue create '<cluster-uuid>' --key '$queue_qemu' --description 'Disposable KVM post-merge acceptance workers' -o json

5. Create the pipeline and GitHub webhook:
   bk pipeline create '$pipeline_name' --repository '$repository' --cluster-uuid '<cluster-uuid>' --create-webhook -o json

STOP: do not connect any agent until Pipeline Settings contain the reviewed static
no-checkout bootstrap and that bootstrap has been statically signed.
EOF
}

if [[ "$apply" != "true" ]]; then
  print_plan
  exit 0
fi

[[ "$confirm_create" == "true" ]] || fail "--apply requires --confirm-create"
command -v bk >/dev/null 2>&1 || fail "bk CLI is unavailable"
command -v python3 >/dev/null 2>&1 || fail "python3 is unavailable"

bk auth switch "$org" >/dev/null
bk auth status -o json >/dev/null

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
    if item.get("name") == cluster_name:
        raise SystemExit("an existing HostPanel cluster was found; inspect it instead of creating a duplicate")

for item in dicts(pipelines):
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

json_field() {
  local raw="$1"
  local label="$2"
  shift 2
  python3 - "$label" "$raw" "$@" <<'PY'
import json
import sys

label, raw, *fields = sys.argv[1:]
try:
    payload = json.loads(raw)
except json.JSONDecodeError as exc:
    raise SystemExit(f"{label} did not return valid JSON") from exc


def find(value):
    if isinstance(value, dict):
        for field in fields:
            candidate = value.get(field)
            if isinstance(candidate, str) and candidate:
                return candidate
        for child in value.values():
            found = find(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = find(child)
            if found:
                return found
    return None


found = find(payload)
if not found:
    raise SystemExit(f"{label} did not contain any of the required fields: {', '.join(fields)}")
print(found)
PY
}

cluster_json="$(bk cluster create \
  --name "$cluster_name" \
  --description 'Disposable hardened HostPanel CI' \
  -o json)"
cluster_uuid="$(json_field "$cluster_json" 'bk cluster create' uuid id)"

upload_queue_json="$(bk queue create "$cluster_uuid" \
  --key "$queue_upload" \
  --description 'Trusted no-checkout pipeline signer/uploader' \
  -o json)"
upload_queue_uuid="$(json_field "$upload_queue_json" 'bk queue create hostpanel-upload' uuid id)"

ci_queue_json="$(bk queue create "$cluster_uuid" \
  --key "$queue_ci" \
  --description 'Disposable repository CI workers' \
  -o json)"
ci_queue_uuid="$(json_field "$ci_queue_json" 'bk queue create hostpanel-ci' uuid id)"

qemu_queue_json="$(bk queue create "$cluster_uuid" \
  --key "$queue_qemu" \
  --description 'Disposable KVM post-merge acceptance workers' \
  -o json)"
qemu_queue_uuid="$(json_field "$qemu_queue_json" 'bk queue create hostpanel-qemu' uuid id)"

pipeline_json="$(bk pipeline create "$pipeline_name" \
  --repository "$repository" \
  --cluster-uuid "$cluster_uuid" \
  --create-webhook \
  -o json)"
pipeline_uuid="$(json_field "$pipeline_json" 'bk pipeline create' uuid id)"
pipeline_slug="$(json_field "$pipeline_json" 'bk pipeline create' slug)"

trap - ERR

cat <<EOF
HostPanel Buildkite control plane created.

organization=$org
cluster_uuid=$cluster_uuid
hostpanel_upload_queue_uuid=$upload_queue_uuid
hostpanel_ci_queue_uuid=$ci_queue_uuid
hostpanel_qemu_queue_uuid=$qemu_queue_uuid
pipeline_uuid=$pipeline_uuid
pipeline_slug=$pipeline_slug

MANDATORY STOP:
Do not connect an agent yet. Replace Pipeline Settings with the reviewed static
no-checkout bootstrap from BUILDKITE.md, generate the signing/verification JWKS,
statically sign Pipeline Settings, and add only the public checkout deploy key
to GitHub before any worker is started.
EOF
