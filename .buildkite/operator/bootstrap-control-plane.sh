#!/usr/bin/env bash
set -euo pipefail

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
unset BASH_ENV ENV PYTHONPATH PYTHONHOME LD_PRELOAD LD_LIBRARY_PATH \
  GIT_CONFIG_GLOBAL GIT_CONFIG_SYSTEM GIT_DIR GIT_WORK_TREE SSH_AUTH_SOCK CDPATH
umask 077

operator_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
operator="$operator_dir/bootstrap-control-plane.py"

[[ -f "$operator" && ! -L "$operator" ]] || {
  printf 'HostPanel Buildkite control-plane operator failed: Python operator is unavailable or unsafe\n' >&2
  exit 1
}
command -v python3 >/dev/null 2>&1 || {
  printf 'HostPanel Buildkite control-plane operator failed: python3 is unavailable\n' >&2
  exit 1
}

exec python3 "$operator" "$@"
