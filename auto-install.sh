#!/usr/bin/env bash
# Fully unattended HostPanel provisioning wrapper.
# Resolves safe defaults, verifies the immutable interactive launcher, invokes
# its preflight/install path, and records machine-readable status.
set -Eeuo pipefail

PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH
umask 077
unset PYTHONPATH PYTHONHOME BASH_ENV ENV LD_PRELOAD LD_LIBRARY_PATH

readonly QUICK_INSTALL_COMMIT="3fc7509b35518d2451f768c5372582087a0eed10"
readonly QUICK_INSTALL_BLOB="54001b0317c5dbd7fc99cb8d48fc418dd4513afc"
readonly PRODUCT_COMMIT="62e356febf2e8a87f9cd4db0518bad140bab7631"
readonly VALIDATOR_BLOB="2eefb797a50a0a2e2827ca5687ba83a2b4b3eec9"
readonly EXPECTED_VERSION="3.4.1"
readonly REPOSITORY_API="https://api.github.com/repos/1-vps/hostpanel"
readonly STATUS_DIR="/var/lib/hostpanel"
readonly STATUS_FILE="$STATUS_DIR/auto-install-status.json"
readonly RUNTIME_DIR="/run/hostpanel-auto-install"
readonly BOOTSTRAP_LOCK_DIR="/run/hostpanel-auto-install.bootstrap.lock"

PANEL_HOST="${HP_PANEL_HOST:-}"
PANEL_DOMAIN="${HP_PANEL_DOMAIN:-}"
ADMIN_CIDR="${HP_PANEL_ADMIN_CIDR:-}"
MTA="${HP_MTA:-postfix}"
REINSTALL="${HP_REINSTALL:-no}"
CHECK_ONLY="${HP_CHECK_ONLY:-no}"
POST_CHECK="${HP_POST_INSTALL_CHECK:-yes}"
TOKEN_FILE="${HP_GITHUB_TOKEN_FILE:-}"
TOKEN_FD="${HP_GITHUB_TOKEN_FD:-}"
TOKEN=""
WORK_DIR=""
AUTH_FILE=""
PRIVATE_TOKEN_FILE=""
PHASE="initializing"
FINAL_STATUS=""
LOCK_FD=""
BOOTSTRAP_LOCK_HELD=no
declare -a ROLES=()

say(){ printf '\n==> %s\n' "$*"; }
die(){ printf '\nError: %s\n' "$*" >&2; exit 1; }

usage(){
  cat <<'HELP'
Usage: sudo bash auto-install.sh [options]

This command never prompts.

Options:
  --hostname FQDN       Panel hostname
  --domain DOMAIN       Use panel.DOMAIN when hostname is not supplied
  --admin-cidr CIDR     Administrative source network
  --mta postfix|exim    Mail transfer agent (default: postfix)
  --role ROLE           Install one role; may be repeated
  --roles LIST          Comma- or space-separated roles
  --reinstall           Explicit reviewed reinstall
  --check-only          Run preflight without packages or persistent installer files
  --skip-post-check     Skip validator and doctor; status is marked unverified
  -h, --help            Show this help

Environment:
  HP_PANEL_HOST, HP_PANEL_DOMAIN, HP_PANEL_ADMIN_CIDR, HP_MTA, HP_ROLES
  HP_REINSTALL=yes, HP_CHECK_ONLY=yes, HP_POST_INSTALL_CHECK=no
  HP_GITHUB_TOKEN_FILE=/root/token or HP_GITHUB_TOKEN_FD=3

Discovered token paths:
  $CREDENTIALS_DIRECTORY/github-token
  /run/secrets/hostpanel_github_token
  /etc/hostpanel/install-github.token
HELP
}

normalize_yes_no(){
  local name="$1" value="${2,,}"
  case "$value" in
    yes|no) printf '%s' "$value" ;;
    1|true|on) printf yes ;;
    0|false|off|'') printf no ;;
    *) die "$name must be yes or no" ;;
  esac
}

append_roles(){
  local value="${1//,/ }" role
  for role in $value; do [[ -z "$role" ]] || ROLES+=("$role"); done
}
[[ -z "${HP_ROLES:-}" ]] || append_roles "$HP_ROLES"

while (($#)); do
  case "$1" in
    --hostname) (($# >= 2)) || die "--hostname requires a value"; PANEL_HOST="$2"; shift 2 ;;
    --domain) (($# >= 2)) || die "--domain requires a value"; PANEL_DOMAIN="$2"; shift 2 ;;
    --admin-cidr) (($# >= 2)) || die "--admin-cidr requires a value"; ADMIN_CIDR="$2"; shift 2 ;;
    --mta) (($# >= 2)) || die "--mta requires a value"; MTA="$2"; shift 2 ;;
    --role) (($# >= 2)) || die "--role requires a value"; ROLES+=("$2"); shift 2 ;;
    --roles) (($# >= 2)) || die "--roles requires a value"; append_roles "$2"; shift 2 ;;
    --reinstall) REINSTALL=yes; shift ;;
    --check-only) CHECK_ONLY=yes; shift ;;
    --skip-post-check) POST_CHECK=no; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "Unknown option: $1" ;;
  esac
done

REINSTALL="$(normalize_yes_no HP_REINSTALL "$REINSTALL")"
CHECK_ONLY="$(normalize_yes_no HP_CHECK_ONLY "$CHECK_ONLY")"
POST_CHECK="$(normalize_yes_no HP_POST_INSTALL_CHECK "$POST_CHECK")"
[[ "$EUID" -eq 0 ]] || die "Run the automatic installer as root"

release_bootstrap_lock(){
  local holder=""
  [[ "$BOOTSTRAP_LOCK_HELD" == yes ]] || return 0
  if [[ -r "$BOOTSTRAP_LOCK_DIR/pid" ]]; then
    IFS= read -r holder <"$BOOTSTRAP_LOCK_DIR/pid" || true
  fi
  if [[ "$holder" == "$$" ]]; then
    rm -f -- "$BOOTSTRAP_LOCK_DIR/pid"
    rmdir -- "$BOOTSTRAP_LOCK_DIR" 2>/dev/null || true
  fi
  BOOTSTRAP_LOCK_HELD=no
}

cleanup(){
  [[ -z "${AUTH_FILE:-}" ]] || rm -f -- "$AUTH_FILE"
  [[ -z "${PRIVATE_TOKEN_FILE:-}" ]] || rm -f -- "$PRIVATE_TOKEN_FILE"
  [[ -z "${WORK_DIR:-}" ]] || rm -rf -- "$WORK_DIR"
  TOKEN=""
  TOKEN_FD=""
  unset TOKEN HP_GITHUB_TOKEN_FILE HP_GITHUB_TOKEN_FD
  release_bootstrap_lock
}

write_status(){
  local state="$1" message="$2" installed="${3:-}"
  python3 - "$STATUS_DIR" "$STATUS_FILE" "$state" "$PHASE" "$message" \
    "$PRODUCT_COMMIT" "$EXPECTED_VERSION" "$installed" "$PANEL_HOST" \
    "$ADMIN_CIDR" "$MTA" "${ROLES[*]:-all}" "$REINSTALL" "$CHECK_ONLY" <<'PY'
import datetime as dt
import json
import os
import pathlib
import secrets
import stat
import sys

(directory_name, status_name, state, phase, message, commit, expected,
 installed, host, cidr, mta, roles, reinstall, check_only) = sys.argv[1:]
directory = pathlib.Path(directory_name)
if directory.exists() or directory.is_symlink():
    metadata = directory.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != 0:
        raise SystemExit("unsafe HostPanel status directory")
    os.chmod(directory, 0o700)
else:
    directory.mkdir(parents=True, mode=0o700)
    os.chown(directory, 0, 0)
payload = {
    "schema": 1,
    "state": state,
    "phase": phase,
    "message": message,
    "updated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
    "product_commit": commit,
    "expected_version": expected,
    "installed_version": installed or None,
    "panel_host": host or None,
    "admin_cidr": cidr or None,
    "mta": mta,
    "roles": roles.split() if roles and roles != "all" else "all",
    "reinstall": reinstall == "yes",
    "check_only": check_only == "yes",
}
temporary = directory / f".auto-install.{os.getpid()}.{secrets.token_hex(8)}"
flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
if hasattr(os, "O_NOFOLLOW"):
    flags |= os.O_NOFOLLOW
fd = os.open(temporary, flags, 0o600)
try:
    data = (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode()
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise SystemExit("could not write installer status")
        view = view[written:]
    os.fsync(fd)
finally:
    os.close(fd)
os.replace(temporary, pathlib.Path(status_name))
os.chown(status_name, 0, 0)
os.chmod(status_name, 0o600)
PY
}

record_status(){
  [[ "$CHECK_ONLY" == no ]] || return 0
  write_status "$@"
}

on_exit(){
  local status=$?
  trap - EXIT
  if ((status != 0)) && [[ "$FINAL_STATUS" != success ]]; then
    record_status failed "Automatic installation failed during $PHASE (exit $status)" \
      "$(cat /opt/hostpanel/VERSION 2>/dev/null || true)" 2>/dev/null || true
  fi
  cleanup
  exit "$status"
}
trap on_exit EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

acquire_bootstrap_lock(){
  local holder="" stale="${BOOTSTRAP_LOCK_DIR}.stale.$$"
  if mkdir -m 0700 -- "$BOOTSTRAP_LOCK_DIR" 2>/dev/null; then
    printf '%s\n' "$$" >"$BOOTSTRAP_LOCK_DIR/pid"
    BOOTSTRAP_LOCK_HELD=yes
    return 0
  fi

  [[ -d "$BOOTSTRAP_LOCK_DIR" && ! -L "$BOOTSTRAP_LOCK_DIR" ]] \
    || die "Unsafe automatic-installer bootstrap lock path"
  [[ "$(stat -c %u -- "$BOOTSTRAP_LOCK_DIR" 2>/dev/null || printf invalid)" == 0 ]] \
    || die "Automatic-installer bootstrap lock is not root-owned"
  if [[ -r "$BOOTSTRAP_LOCK_DIR/pid" ]]; then
    IFS= read -r holder <"$BOOTSTRAP_LOCK_DIR/pid" || true
  fi
  if [[ "$holder" =~ ^[1-9][0-9]*$ && -d "/proc/$holder" ]]; then
    die "Another HostPanel installation is already starting (pid $holder)"
  fi

  mv -T -- "$BOOTSTRAP_LOCK_DIR" "$stale" 2>/dev/null \
    || die "Could not recover a stale automatic-installer bootstrap lock"
  rm -rf -- "$stale"
  mkdir -m 0700 -- "$BOOTSTRAP_LOCK_DIR" \
    || die "Could not acquire the automatic-installer bootstrap lock"
  printf '%s\n' "$$" >"$BOOTSTRAP_LOCK_DIR/pid"
  BOOTSTRAP_LOCK_HELD=yes
}

ensure_prerequisites(){
  local command missing=()
  for command in curl git python3 mktemp flock hostname; do
    command -v "$command" >/dev/null 2>&1 || missing+=("$command")
  done
  ((${#missing[@]} == 0)) && return 0

  if [[ "$CHECK_ONLY" == yes ]]; then
    die "Missing commands (${missing[*]}); check-only never installs packages"
  fi

  say "Installing automatic-installer prerequisites"
  if command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get -o Acquire::Retries=3 update -qq
    apt-get -o Acquire::Retries=3 install -y -qq ca-certificates curl git python3 coreutils util-linux hostname
  elif command -v dnf >/dev/null 2>&1; then
    dnf -y --setopt=retries=3 install ca-certificates curl git python3 coreutils util-linux hostname
  else
    die "Missing commands (${missing[*]}) and no supported package manager was found"
  fi
  for command in curl git python3 mktemp flock hostname; do
    command -v "$command" >/dev/null 2>&1 || die "$command is still unavailable"
  done
}

acquire_lock(){
  install -d -o root -g root -m 0700 "$RUNTIME_DIR"
  exec {LOCK_FD}>"$RUNTIME_DIR/install.lock"
  flock -n "$LOCK_FD" || die "Another HostPanel installation is already running"
}

validate_hostname(){
  python3 - "$1" <<'PY'
import ipaddress, re, sys
value = sys.argv[1].strip().lower().rstrip('.')
if not value or len(value) > 253 or '.' not in value or value.endswith('.localdomain'):
    raise SystemExit(1)
try:
    ipaddress.ip_address(value)
except ValueError:
    pass
else:
    raise SystemExit(1)
label = re.compile(r'^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$')
if any(not label.fullmatch(part) for part in value.split('.')):
    raise SystemExit(1)
print(value)
PY
}

validate_cidr(){
  python3 - "$1" <<'PY'
import ipaddress, sys
value = sys.argv[1].strip()
if '/' not in value:
    raise SystemExit(1)
print(ipaddress.ip_network(value, strict=False).with_prefixlen)
PY
}

detect_host(){
  local candidate
  for candidate in "$(hostname -f 2>/dev/null || true)" "$(hostname --fqdn 2>/dev/null || true)"; do
    [[ -n "$candidate" ]] || continue
    validate_hostname "$candidate" 2>/dev/null && return 0
  done
  return 1
}

detect_cidr(){
  python3 - "${SSH_CONNECTION:-}" "${SSH_CLIENT:-}" <<'PY'
import ipaddress, sys
for value in sys.argv[1:]:
    parts = value.split()
    if not parts:
        continue
    try:
        address = ipaddress.ip_address(parts[0])
    except ValueError:
        continue
    print(f'{address}/{32 if address.version == 4 else 128}')
    raise SystemExit(0)
raise SystemExit(1)
PY
}

resolve_inputs(){
  local role
  if [[ -n "$PANEL_HOST" ]]; then
    PANEL_HOST="$(validate_hostname "$PANEL_HOST")" || die "Invalid panel hostname"
  elif [[ -n "$PANEL_DOMAIN" ]]; then
    PANEL_HOST="$(validate_hostname "panel.${PANEL_DOMAIN#.}")" \
      || die "Invalid HP_PANEL_DOMAIN: $PANEL_DOMAIN"
  else
    PANEL_HOST="$(detect_host 2>/dev/null || true)"
    [[ -n "$PANEL_HOST" ]] || die "Could not detect a valid FQDN; set HP_PANEL_HOST or HP_PANEL_DOMAIN"
  fi

  [[ -n "$ADMIN_CIDR" ]] || ADMIN_CIDR="$(detect_cidr 2>/dev/null || true)"
  [[ -n "$ADMIN_CIDR" ]] || die "Could not detect an SSH source; set HP_PANEL_ADMIN_CIDR"
  ADMIN_CIDR="$(validate_cidr "$ADMIN_CIDR")" || die "Invalid administrative CIDR"

  case "$MTA" in postfix|exim) ;; *) die "MTA must be postfix or exim" ;; esac
  declare -A seen=()
  local -a normalized=()
  for role in "${ROLES[@]}"; do
    case "$role" in control|web|database|mail|dns|backup|edge) ;; *) die "Unsupported role: $role" ;; esac
    [[ -n "${seen[$role]:-}" ]] || normalized+=("$role")
    seen[$role]=1
  done
  ROLES=("${normalized[@]}")
}

resolve_token_source(){
  local candidate
  [[ -n "$TOKEN_FILE" || -n "$TOKEN_FD" ]] && return 0
  if [[ -n "${CREDENTIALS_DIRECTORY:-}" && -e "$CREDENTIALS_DIRECTORY/github-token" ]]; then
    TOKEN_FILE="$CREDENTIALS_DIRECTORY/github-token"; return 0
  fi
  for candidate in /run/secrets/hostpanel_github_token /etc/hostpanel/install-github.token; do
    [[ ! -e "$candidate" ]] || { TOKEN_FILE="$candidate"; return 0; }
  done
}

read_token_file(){
  python3 - "$1" <<'PY'
import os, pathlib, stat, sys
path = pathlib.Path(sys.argv[1])
flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, 'O_NOFOLLOW', 0)
fd = os.open(path, flags)
try:
    before = os.fstat(fd)
    if not stat.S_ISREG(before.st_mode) or before.st_uid != 0 or before.st_nlink != 1:
        raise SystemExit('token file must be a root-owned single-linked regular file')
    if stat.S_IMODE(before.st_mode) not in {0o400, 0o600} or not 20 <= before.st_size <= 512:
        raise SystemExit('token file must be mode 0400/0600 with a safe size')
    data = bytearray()
    while len(data) <= 512:
        chunk = os.read(fd, 513 - len(data))
        if not chunk:
            break
        data.extend(chunk)
    after = os.fstat(fd)
    current = os.lstat(path)
    if len(data) != before.st_size or (before.st_dev, before.st_ino, before.st_size) != (after.st_dev, after.st_ino, after.st_size):
        raise SystemExit('token file changed while being read')
    if (after.st_dev, after.st_ino) != (current.st_dev, current.st_ino):
        raise SystemExit('token file identity changed')
    token = bytes(data).decode('ascii', errors='strict')
    if '\n' in token or '\r' in token:
        raise SystemExit('token file must not contain a newline')
    print(token, end='')
finally:
    os.close(fd)
PY
}

read_token(){
  resolve_token_source
  if [[ -n "$TOKEN_FD" ]]; then
    [[ "$TOKEN_FD" =~ ^[0-9]+$ ]] || die "HP_GITHUB_TOKEN_FD must be numeric"
    IFS= read -r TOKEN <&"$TOKEN_FD" || [[ -n "$TOKEN" ]] || die "Could not read token descriptor"
    eval "exec ${TOKEN_FD}<&-"
    TOKEN_FD=""
    unset HP_GITHUB_TOKEN_FD
  elif [[ -n "$TOKEN_FILE" ]]; then
    TOKEN="$(read_token_file "$TOKEN_FILE")" || die "Could not read token file safely"
  else
    die "Set HP_GITHUB_TOKEN_FILE/FD or provide a discovered root-only secret"
  fi
  [[ "$TOKEN" =~ ^[A-Za-z0-9_]{20,512}$ ]] || die "GitHub token has an unsafe format"
}

prepare_downloads(){
  WORK_DIR="$(mktemp -d /tmp/hostpanel-auto.XXXXXX)" || die "Could not create private work directory"
  chmod 0700 "$WORK_DIR"
  PRIVATE_TOKEN_FILE="$WORK_DIR/github.token"
  printf '%s' "$TOKEN" >"$PRIVATE_TOKEN_FILE"
  chmod 0600 "$PRIVATE_TOKEN_FILE"
  AUTH_FILE="$WORK_DIR/curl.conf"
  {
    printf 'header = "Accept: application/vnd.github.raw+json"\n'
    printf 'header = "Authorization: Bearer %s"\n' "$TOKEN"
    printf 'header = "X-GitHub-Api-Version: 2022-11-28"\n'
  } >"$AUTH_FILE"
  chmod 0600 "$AUTH_FILE"
  TOKEN=""
}

ensure_download_context(){
  [[ -n "$AUTH_FILE" && -f "$AUTH_FILE" ]] && return 0
  read_token
  prepare_downloads
}

download_file(){
  local path="$1" ref="$2" destination="$3"
  curl -q --proto '=https' --tlsv1.2 --fail --location --silent --show-error \
    --retry 5 --retry-all-errors --retry-delay 2 --connect-timeout 15 --max-time 240 \
    --config "$AUTH_FILE" "${REPOSITORY_API}/contents/${path}?ref=${ref}" -o "$destination"
}

ensure_validator(){
  if [[ -f /root/validate-production-vm.sh ]] && \
     [[ "$(git hash-object /root/validate-production-vm.sh 2>/dev/null || true)" == "$VALIDATOR_BLOB" ]]; then
    return 0
  fi
  ensure_download_context
  download_file tools/validate-production-vm.sh "$PRODUCT_COMMIT" "$WORK_DIR/validator.sh"
  [[ "$(git hash-object "$WORK_DIR/validator.sh")" == "$VALIDATOR_BLOB" ]] || die "Validator blob verification failed"
  bash -n "$WORK_DIR/validator.sh"
  install -o root -g root -m 0700 "$WORK_DIR/validator.sh" /root/validate-production-vm.sh
}

post_check(){
  local installed
  installed="$(cat /opt/hostpanel/VERSION 2>/dev/null || true)"
  [[ "$installed" == "$EXPECTED_VERSION" ]] || die "Installed version is $installed; expected $EXPECTED_VERSION"
  [[ "$POST_CHECK" == yes ]] || return 0
  ensure_validator
  [[ -x /opt/hostpanel/venv/bin/python ]] || die "HostPanel Python environment is missing"
  [[ -f /opt/hostpanel/app/hostpanel-doctor ]] || die "hostpanel-doctor is missing"
  env HP_EXPECTED_VERSION="$EXPECTED_VERSION" HP_PANEL_HOST="$PANEL_HOST" \
    bash /root/validate-production-vm.sh --check
  /opt/hostpanel/venv/bin/python /opt/hostpanel/app/hostpanel-doctor --quiet
}

acquire_bootstrap_lock
ensure_prerequisites
acquire_lock
PHASE="resolving-inputs"
resolve_inputs
record_status running "Resolved fully automatic installation inputs"

installed="$(cat /opt/hostpanel/VERSION 2>/dev/null || true)"
if [[ -n "$installed" && "$REINSTALL" == no && "$CHECK_ONLY" == no ]]; then
  [[ "$installed" == "$EXPECTED_VERSION" ]] || die "HostPanel $installed is installed; set HP_REINSTALL=yes explicitly"
  PHASE="verifying-existing-installation"
  post_check
  FINAL_STATUS=success
  if [[ "$POST_CHECK" == yes ]]; then
    record_status success "Existing HostPanel installation is healthy" "$installed"
    printf '\nHostPanel %s is already installed and healthy.\n' "$installed"
  else
    record_status unverified "Existing HostPanel version matches; validation was skipped" "$installed"
    printf '\nHostPanel %s is already installed; validation was skipped.\n' "$installed"
  fi
  exit 0
fi

PHASE="reading-credentials"
ensure_download_context
PHASE="downloading-launcher"
download_file quick-install.sh "$QUICK_INSTALL_COMMIT" "$WORK_DIR/quick-install.sh"
[[ "$(git hash-object "$WORK_DIR/quick-install.sh")" == "$QUICK_INSTALL_BLOB" ]] || die "Quick installer blob verification failed"
bash -n "$WORK_DIR/quick-install.sh"
chmod 0700 "$WORK_DIR/quick-install.sh"

args=(--hostname "$PANEL_HOST" --admin-cidr "$ADMIN_CIDR" --mta "$MTA" --yes)
[[ "$REINSTALL" == no ]] || args+=(--reinstall)
[[ "$CHECK_ONLY" == no ]] || args+=(--check-only)
for role in "${ROLES[@]}"; do args+=(--role "$role"); done

PHASE="preflight-and-install"
record_status running "Running verified HostPanel preflight and installation"
env -u HP_GITHUB_TOKEN_FD HP_GITHUB_TOKEN_FILE="$PRIVATE_TOKEN_FILE" \
  bash "$WORK_DIR/quick-install.sh" "${args[@]}"

if [[ "$CHECK_ONLY" == yes ]]; then
  FINAL_STATUS=success
  printf '\nAutomatic preflight passed. No packages or persistent installer files were changed.\n'
  exit 0
fi

PHASE="post-install-validation"
post_check
installed="$(cat /opt/hostpanel/VERSION 2>/dev/null || true)"
FINAL_STATUS=success
if [[ "$POST_CHECK" == yes ]]; then
  record_status success "HostPanel automatic installation and validation completed" "$installed"
  printf '\nHostPanel %s installed automatically and validated.\n' "$installed"
else
  record_status unverified "HostPanel automatic installation completed; validation was skipped" "$installed"
  printf '\nHostPanel %s installed automatically; validation was skipped.\n' "$installed"
fi
printf 'Status: %s\n' "$STATUS_FILE"
printf 'Log: /var/log/hostpanel-install.log\n'
