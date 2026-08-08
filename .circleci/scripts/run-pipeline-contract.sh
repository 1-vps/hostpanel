#!/usr/bin/env bash
set -euo pipefail

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
unset ENV PYTHONPATH PYTHONHOME LD_PRELOAD LD_LIBRARY_PATH

root="$(git rev-parse --show-toplevel)"
cd "$root"
.circleci/scripts/verify-build.sh

command -v python3 >/dev/null 2>&1 || {
  printf 'CircleCI pipeline contract failed: python3 is unavailable\n' >&2
  exit 1
}
command -v circleci >/dev/null 2>&1 || {
  printf 'CircleCI pipeline contract failed: CircleCI CLI is unavailable\n' >&2
  exit 1
}
python3 -c 'import yaml' >/dev/null 2>&1 || {
  printf 'CircleCI pipeline contract failed: Python module yaml is unavailable\n' >&2
  exit 1
}
python3 -m unittest -v tests.test_circleci_pipeline
