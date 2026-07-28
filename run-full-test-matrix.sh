#!/usr/bin/env bash
# Run HostPanel's deterministic installer validation and supported-OS matrix.
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
QUICK=no
VERBOSE=no
TARGET_OS=""

usage(){
  cat <<'EOF'
Usage: ./run-full-test-matrix.sh [--quick] [--os OS] [--verbose]

--quick     Test Ubuntu 26.04, Debian 13 and Rocky Linux 10 after static checks.
--os OS     Test one supported OS.
--verbose   Show detailed container output.
EOF
}

while (($#)); do
  case "$1" in
    --quick) QUICK=yes ;;
    --verbose) VERBOSE=yes ;;
    --os) shift; (($#)) || { usage >&2; exit 2; }; TARGET_OS="$1" ;;
    --os=*) TARGET_OS="${1#*=}" ;;
    --help|-h) usage; exit 0 ;;
    *) printf 'Unknown argument: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

args=()
[[ "$VERBOSE" == yes ]] && args+=(--verbose)

"$SCRIPT_DIR/test-matrix.sh" --check-only "${args[@]}"

if [[ -n "$TARGET_OS" ]]; then
  exec "$SCRIPT_DIR/test-matrix.sh" --os "$TARGET_OS" "${args[@]}"
fi

if [[ "$QUICK" == yes ]]; then
  for os_name in ubuntu-26.04 debian-13 rocky-10; do
    "$SCRIPT_DIR/test-matrix.sh" --os "$os_name" "${args[@]}"
  done
  exit 0
fi

exec "$SCRIPT_DIR/test-matrix.sh" "${args[@]}"
