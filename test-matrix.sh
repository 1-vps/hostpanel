#!/usr/bin/env bash
# Validate the complete HostPanel installer bundle across supported OS images.
# Container jobs cover generation, Bash syntax, OS policy, input validation,
# preflight, dry-run semantics and selected package candidates. They do not
# claim to be full systemd installations.
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
LOG_DIR="$SCRIPT_DIR/test-logs"
CHECK_ONLY=no
VERBOSE=no
TARGET_OS=""

SUPPORTED_OSES=(
  ubuntu-22.04 ubuntu-24.04 ubuntu-26.04
  debian-12 debian-13 rocky-9 rocky-10 almalinux-9 almalinux-10
)
declare -A OS_IMAGES=(
  [ubuntu-22.04]="ubuntu:22.04"
  [ubuntu-24.04]="ubuntu:24.04"
  [ubuntu-26.04]="ubuntu:26.04"
  [debian-12]="debian:12"
  [debian-13]="debian:13"
  [rocky-9]="rockylinux/rockylinux:9"
  [rocky-10]="rockylinux/rockylinux:10"
  [almalinux-9]="almalinux:9"
  [almalinux-10]="almalinux:10"
)
declare -A PKG_FAMILY=(
  [ubuntu-22.04]=debian [ubuntu-24.04]=debian [ubuntu-26.04]=debian
  [debian-12]=debian [debian-13]=debian
  [rocky-9]=rhel [rocky-10]=rhel [almalinux-9]=rhel [almalinux-10]=rhel
)

usage(){
  cat <<'EOF'
Usage: ./test-matrix.sh [--check-only] [--verbose] [--os OS]

--check-only  Run deterministic generation, Bash syntax and Python regressions.
--os OS       Test one supported Docker image.
--verbose     Show container command output.
EOF
}

while (($#)); do
  case "$1" in
    --check-only) CHECK_ONLY=yes ;;
    --verbose) VERBOSE=yes ;;
    --os) shift; (($#)) || { usage >&2; exit 2; }; TARGET_OS="$1" ;;
    --os=*) TARGET_OS="${1#*=}" ;;
    --help|-h) usage; exit 0 ;;
    *) printf 'Unknown argument: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

log(){ printf '[matrix] %s\n' "$*" >&2; }
verbose(){ [[ "$VERBOSE" == yes ]] && printf '[matrix:debug] %s\n' "$*" >&2 || true; }
contains_os(){ local item; for item in "${SUPPORTED_OSES[@]}"; do [[ "$item" == "$1" ]] && return 0; done; return 1; }

mkdir -p "$LOG_DIR"

validate_bundle(){
  local generated
  generated="$(mktemp "$LOG_DIR/install.generated.XXXXXX")"
  trap 'rm -f -- "$generated"' RETURN
  python3 "$SCRIPT_DIR/tools/harden_install.py" \
    "$SCRIPT_DIR/install.base.sh" "$generated"
  bash -n "$SCRIPT_DIR/install.sh"
  bash -n "$SCRIPT_DIR/bootstrap-install.sh"
  bash -n "$generated"
  python3 -m unittest discover -s "$SCRIPT_DIR/tests" -p 'test_*.py' -v
  grep -q 'INSTALL_SNAPSHOT_DIR="/var/backups/hostpanel-install"' "$generated"
  grep -q 'user default off' "$generated"
  grep -q 'systemd-run --quiet' "$generated"
  ! grep -q 'https://repo.litespeed.sh' "$generated"
  rm -f -- "$generated"
  trap - RETURN
  log "deterministic generation and regressions passed"
}

install_prerequisites(){
  local os_name="$1" cid="$2" family="${PKG_FAMILY[$1]}"
  if [[ "$family" == debian ]]; then
    docker exec -e DEBIAN_FRONTEND=noninteractive "$cid" sh -ceu '
      apt-get update -qq
      apt-get install -y -qq --no-install-recommends bash python3 git ca-certificates coreutils grep gawk hostname
    '
  else
    docker exec "$cid" sh -ceu '
      dnf -y -q install bash python3 git ca-certificates coreutils-single grep gawk hostname findutils
    '
  fi
  verbose "$os_name prerequisites installed"
}

copy_bundle(){
  local cid="$1"
  docker exec "$cid" mkdir -p /root/hostpanel/tools
  docker cp "$SCRIPT_DIR/install.sh" "$cid:/root/hostpanel/install.sh" >/dev/null
  docker cp "$SCRIPT_DIR/install.base.sh" "$cid:/root/hostpanel/install.base.sh" >/dev/null
  docker cp "$SCRIPT_DIR/tools/harden_install.py" "$cid:/root/hostpanel/tools/harden_install.py" >/dev/null
  docker cp "$SCRIPT_DIR/tools/harden_install_runtime.py" "$cid:/root/hostpanel/tools/harden_install_runtime.py" >/dev/null
}

run_expect_success(){
  local cid="$1" name="$2"; shift 2
  local output
  if ! output="$(docker exec "$cid" "$@" 2>&1)"; then
    printf '%s\n' "$output" >"$LOG_DIR/${name}.log"
    printf 'FAIL %s; see %s\n' "$name" "$LOG_DIR/${name}.log" >&2
    return 1
  fi
  [[ "$VERBOSE" == yes ]] && printf '%s\n' "$output" >&2
}

run_container_test(){
  local os_name="$1" image="${OS_IMAGES[$1]}" cid output
  log "$os_name: pulling $image"
  docker pull "$image" >/dev/null
  cid="$(docker run -d --rm --tmpfs /tmp:exec --tmpfs /run:exec --memory 2g --cpus 2 "$image" sleep 3600)"
  trap 'docker rm -f "$cid" >/dev/null 2>&1 || true' RETURN
  install_prerequisites "$os_name" "$cid"
  copy_bundle "$cid"

  run_expect_success "$cid" "$os_name-generate" sh -ceu '
    cd /root/hostpanel
    python3 tools/harden_install.py install.base.sh /tmp/install.generated.sh
    bash -n install.sh
    bash -n /tmp/install.generated.sh
  '

  output="$(docker exec \
    -e HP_PANEL_HOST=panel.example.test \
    -e HP_ALLOW_EXISTING_STACK=yes \
    -e HP_MULTI_PHP_REPO=off \
    -e HP_RSPAMD_REPO=off \
    "$cid" bash /root/hostpanel/install.sh --check --role web 2>&1)" || {
      printf '%s\n' "$output" >"$LOG_DIR/${os_name}-preflight.log"
      return 1
    }
  grep -q 'Preflight passed' <<<"$output"

  if docker exec -e HP_PANEL_HOST=localhost "$cid" \
      bash /root/hostpanel/install.sh --check --role web \
      >"$LOG_DIR/${os_name}-invalid-fqdn.log" 2>&1; then
    printf 'invalid FQDN accepted on %s\n' "$os_name" >&2
    return 1
  fi
  grep -q 'valid FQDN' "$LOG_DIR/${os_name}-invalid-fqdn.log"

  output="$(docker exec \
    -e HP_PANEL_HOST=panel.example.test \
    -e HP_ALLOW_EXISTING_STACK=yes \
    "$cid" bash /root/hostpanel/install.sh --dry-run --role web 2>&1)" || {
      printf '%s\n' "$output" >"$LOG_DIR/${os_name}-dry-run.log"
      return 1
    }
  grep -q 'No changes were made' <<<"$output"

  if [[ "$os_name" == ubuntu-26.04 ]]; then
    docker exec "$cid" sh -ceu '
      for package in python3-venv php8.5-fpm php8.5-cli rspamd postgresql; do
        candidate=$(apt-cache policy "$package" | awk "/Candidate:/ {print \$2; exit}")
        test -n "$candidate" && test "$candidate" != "(none)"
      done
      candidate=$(apt-cache policy postgresql-contrib | awk "/Candidate:/ {print \$2; exit}")
      test -z "$candidate" || test "$candidate" = "(none)"
    '
  fi

  docker rm -f "$cid" >/dev/null
  trap - RETURN
  log "$os_name: bundle generation, preflight, rejection and dry-run passed"
}

validate_bundle
[[ "$CHECK_ONLY" == yes ]] && exit 0
command -v docker >/dev/null 2>&1 || { printf 'Docker is required for the OS matrix\n' >&2; exit 1; }
docker info >/dev/null 2>&1 || { printf 'Docker daemon is unavailable\n' >&2; exit 1; }

if [[ -n "$TARGET_OS" ]]; then
  contains_os "$TARGET_OS" || { printf 'Unsupported matrix OS: %s\n' "$TARGET_OS" >&2; exit 2; }
  run_container_test "$TARGET_OS"
else
  failures=0
  for os_name in "${SUPPORTED_OSES[@]}"; do
    run_container_test "$os_name" || failures=$((failures + 1))
  done
  ((failures == 0)) || { printf '%d OS matrix job(s) failed\n' "$failures" >&2; exit 1; }
fi
