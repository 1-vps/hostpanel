#!/usr/bin/env bash
set -euo pipefail

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
unset BASH_ENV ENV PYTHONPATH PYTHONHOME LD_PRELOAD LD_LIBRARY_PATH

root="$(git rev-parse --show-toplevel)"
cd "$root"

.buildkite/scripts/verify-build.sh
command -v buildkite-agent >/dev/null 2>&1 || {
  printf 'Buildkite pipeline contract failed: buildkite-agent is unavailable\n' >&2
  exit 1
}
command -v python3 >/dev/null 2>&1 || {
  printf 'Buildkite pipeline contract failed: python3 is unavailable\n' >&2
  exit 1
}
python3 -c 'import yaml' >/dev/null 2>&1 || {
  printf 'Buildkite pipeline contract failed: Python module yaml is unavailable; install python3-yaml\n' >&2
  exit 1
}

rendered_pipeline="$(mktemp /tmp/hostpanel-buildkite-pipeline.XXXXXX.yml)"
cleanup() { rm -f -- "$rendered_pipeline"; }
trap cleanup EXIT

buildkite-agent pipeline upload \
  --dry-run \
  --format yaml \
  --reject-secrets \
  --reject-parse-warnings \
  .buildkite/pipeline.yml >"$rendered_pipeline"

python3 - "$rendered_pipeline" <<'PY'
import pathlib, sys, yaml
payload = yaml.safe_load(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
if not isinstance(payload, dict) or not isinstance(payload.get("steps"), list):
    raise SystemExit("rendered Buildkite pipeline is not a step document")
PY

python3 -m unittest -v \
  tests.test_buildkite_pipeline \
  tests.test_buildkite_prebootstrap_policy \
  tests.test_buildkite_checkout_policy \
  tests.test_buildkite_private_checkout \
  tests.test_buildkite_ephemeral_service \
  tests.test_buildkite_provisioning_source \
  tests.test_buildkite_agent_token
