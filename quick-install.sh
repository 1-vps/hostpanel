#!/usr/bin/env bash
# DirectAdmin-style HostPanel installer for the private repository.
# Installs the reviewed nginx+Apache base and the verified CustomBuild tool.
set -Eeuo pipefail

PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH
umask 077
unset PYTHONPATH PYTHONHOME BASH_ENV ENV LD_PRELOAD LD_LIBRARY_PATH

readonly REVIEWED_COMMIT_SHA="755dcd5e47b7c82404b267e8df4dec27626fe341"
readonly BOOTSTRAP_BLOB="639fae60ddd5bec36f5e3167dd21733a412a69fd"
readonly VALIDATOR_BLOB="2eefb797a50a0a2e2827ca5687ba83a2b4b3eec9"
readonly REPOSITORY_API="https://api.github.com/repos/1-vps/hostpanel"
readonly CUSTOMBUILD_PATHS=(
  tools/hostpanel-build.py
  tools/hostpanel_build_config.py
  tools/hostpanel_build_packages.py
  tools/hostpanel_build_operations.py
  tools/hostpanel_build_cli.py
  tools/hostpanel_build_ssl.py
  tools/hostpanel_build_web.py
  tools/patch_custombuild_runtime.py
  tools/install-hostpanel-build.sh
)
readonly CUSTOMBUILD_BLOBS=(
  de2f5aac8ea7880f341ff93851eb744a5fcb0bda
  d14924e9f0fbeca229097cd9a38e638f452f54cf
  d2b49d6da0735b144c4937b6fa26ec546afc14f6
  ebd99bea7b0c9bea81f736ce56e2e0d04a42dce0
  6a4633a5e2ba99093e8e2ba9e8af94a124970c3e
  9cc63f3e8b0a49464b89b594bec2c3467ff9428d
  674144e2628b3048c51bf3487d781a5aeb07e73d
  c3afb152058419d0bf397c18f0bf7c9c5b0121dd
  7a0ad9c287d1000c466d358409ccc7cf856f4313
)

PANEL_HOST="${HP_PANEL_HOST:-}"
ADMIN_CIDR="${HP_PANEL_ADMIN_CIDR:-}"
MTA="${HP_MTA:-postfix}"
REINSTALL=no
CHECK_ONLY=no
ASSUME_YES=no
TOKEN=""
AUTH_FILE=""
WORK_DIR=""
PRODUCT_DIR=""
TOKEN_FD="${HP_GITHUB_TOKEN_FD:-}"
TOKEN_FILE="${HP_GITHUB_TOKEN_FILE:-}"
declare -a ROLES=()

say(){ printf '\n==> %s\n' "$*"; }
die(){ printf '\nError: %s\n' "$*" >&2; exit 1; }

usage(){
  cat <<'HELP'
Usage: sudo bash quick-install.sh [options]

Options:
  --hostname FQDN       Panel hostname, for example panel.example.com
  --admin-cidr CIDR     Administrative source, for example 192.0.2.10/32
  --mta postfix|exim    Mail transfer agent (default: postfix)
  --role ROLE           Install one role; may be repeated
  --reinstall           Run the reviewed reinstall path
  --check-only          Run preflight without installing packages or persistent files
  --yes                 Skip the final confirmation
  -h, --help            Show this help

The base web stack is always nginx_apache: nginx is public and Apache is the
private backend. After installation use hostpanel-build to select nginx,
Apache, OpenLiteSpeed, or nginx_apache.

Authentication:
  By default the installer prompts without echo for a short-lived GitHub token
  with Contents: Read-only access to 1-vps/hostpanel.

  Automation may provide a root-owned mode-0600 token file through
  HP_GITHUB_TOKEN_FILE, or a numeric inherited descriptor through
  HP_GITHUB_TOKEN_FD. The token is never placed in a URL or command argument.
HELP
}

cleanup(){
  local status=$?
  [[ -z "${AUTH_FILE:-}" ]] || rm -f -- "$AUTH_FILE"
  [[ -z "${WORK_DIR:-}" ]] || rm -rf -- "$WORK_DIR"
  TOKEN=""
  TOKEN_FD=""
  unset TOKEN HP_GITHUB_TOKEN HP_GITHUB_TOKEN_FILE HP_GITHUB_TOKEN_FD
  unset GIT_CONFIG_COUNT GIT_CONFIG_KEY_0 GIT_CONFIG_VALUE_0 GIT_TERMINAL_PROMPT
  return "$status"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

while (($#)); do
  case "$1" in
    --hostname)
      (($# >= 2)) || die "--hostname requires a value"
      PANEL_HOST="$2"; shift 2 ;;
    --admin-cidr)
      (($# >= 2)) || die "--admin-cidr requires a value"
      ADMIN_CIDR="$2"; shift 2 ;;
    --mta)
      (($# >= 2)) || die "--mta requires a value"
      MTA="$2"; shift 2 ;;
    --role)
      (($# >= 2)) || die "--role requires a value"
      ROLES+=("$2"); shift 2 ;;
    --reinstall) REINSTALL=yes; shift ;;
    --check-only) CHECK_ONLY=yes; shift ;;
    --yes) ASSUME_YES=yes; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "Unknown option: $1" ;;
  esac
done

[[ "$EUID" -eq 0 ]] || die "Run the installer as root"

ensure_prerequisites(){
  local missing=() command
  for command in curl git openssl python3 base64 mktemp; do
    command -v "$command" >/dev/null 2>&1 || missing+=("$command")
  done
  ((${#missing[@]} == 0)) && return 0
  [[ "$CHECK_ONLY" != yes ]] \
    || die "Missing commands (${missing[*]}); check-only never installs packages"

  say "Installing bootstrap prerequisites"
  if command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get -o Acquire::Retries=3 update -qq
    apt-get -o Acquire::Retries=3 install -y -qq \
      ca-certificates curl git openssl python3 coreutils
  elif command -v dnf >/dev/null 2>&1; then
    dnf -y --setopt=retries=3 install \
      ca-certificates curl git openssl python3 coreutils
  else
    die "Missing commands (${missing[*]}) and no supported package manager was found"
  fi
  for command in curl git openssl python3 base64 mktemp; do
    command -v "$command" >/dev/null 2>&1 || die "$command is still unavailable"
  done
}

validate_hostname(){
  python3 - "$1" <<'PY'
import ipaddress
import re
import sys
value = sys.argv[1].strip().lower().rstrip(".")
if not value or len(value) > 253 or "." not in value or value.endswith(".localdomain"):
    raise SystemExit(1)
try:
    ipaddress.ip_address(value)
except ValueError:
    pass
else:
    raise SystemExit(1)
label = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
if any(not label.fullmatch(part) for part in value.split(".")):
    raise SystemExit(1)
print(value)
PY
}

validate_cidr(){
  python3 - "$1" <<'PY'
import ipaddress
import sys
value = sys.argv[1].strip()
if "/" not in value:
    raise SystemExit(1)
print(ipaddress.ip_network(value, strict=False).with_prefixlen)
PY
}

default_admin_cidr(){
  python3 - "${SSH_CONNECTION:-}" <<'PY'
import ipaddress
import sys
parts = sys.argv[1].split()
if not parts:
    raise SystemExit(0)
try:
    address = ipaddress.ip_address(parts[0])
except ValueError:
    raise SystemExit(0)
print(f"{address}/{32 if address.version == 4 else 128}")
PY
}

read_token(){
  if [[ -n "$TOKEN_FD" ]]; then
    [[ "$TOKEN_FD" =~ ^[0-9]+$ ]] || die "HP_GITHUB_TOKEN_FD must be numeric"
    IFS= read -r TOKEN <&"$TOKEN_FD" || [[ -n "$TOKEN" ]] \
      || die "Could not read the GitHub token descriptor"
    eval "exec ${TOKEN_FD}<&-"
    TOKEN_FD=""
    unset HP_GITHUB_TOKEN_FD
  elif [[ -n "$TOKEN_FILE" ]]; then
    TOKEN="$(python3 - "$TOKEN_FILE" <<'PYTOKEN'
import os
import pathlib
import stat
import sys
path = pathlib.Path(sys.argv[1])
flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
try:
    descriptor = os.open(path, flags)
except OSError as exc:
    raise SystemExit(f"Could not safely open HP_GITHUB_TOKEN_FILE: {exc}")
try:
    before = os.fstat(descriptor)
    if not stat.S_ISREG(before.st_mode):
        raise SystemExit("HP_GITHUB_TOKEN_FILE must be a regular file")
    if before.st_uid != 0 or stat.S_IMODE(before.st_mode) != 0o600 or before.st_nlink != 1:
        raise SystemExit("HP_GITHUB_TOKEN_FILE must be root-owned, single-linked and mode 0600")
    if before.st_size < 20 or before.st_size > 512:
        raise SystemExit("HP_GITHUB_TOKEN_FILE has an unsafe size")
    data = bytearray()
    while len(data) <= 512:
        chunk = os.read(descriptor, 513 - len(data))
        if not chunk:
            break
        data.extend(chunk)
    after = os.fstat(descriptor)
    path_state = os.lstat(path)
    if ((before.st_dev, before.st_ino, before.st_size)
        != (after.st_dev, after.st_ino, after.st_size)
        or (after.st_dev, after.st_ino) != (path_state.st_dev, path_state.st_ino)):
        raise SystemExit("HP_GITHUB_TOKEN_FILE changed while it was being read")
    if len(data) != before.st_size:
        raise SystemExit("HP_GITHUB_TOKEN_FILE could not be read completely")
    token = bytes(data).decode("ascii", errors="strict")
    if "\n" in token or "\r" in token:
        raise SystemExit("HP_GITHUB_TOKEN_FILE must not contain a newline")
    print(token, end="")
finally:
    os.close(descriptor)
PYTOKEN
    )" || die "Could not read HP_GITHUB_TOKEN_FILE safely"
  else
    [[ -r /dev/tty ]] || die "No terminal is available for the hidden GitHub token prompt"
    read -r -s -p 'GitHub Contents:Read token: ' TOKEN </dev/tty
    printf '\n' >/dev/tty
  fi
  [[ "$TOKEN" =~ ^[A-Za-z0-9_]{20,512}$ ]] \
    || die "The GitHub token contains unsupported characters or has an unsafe length"
}

prompt_inputs(){
  local detected="" role
  if [[ -z "$PANEL_HOST" ]]; then
    read -r -p "Panel hostname (for example panel.example.com): " PANEL_HOST </dev/tty
  fi
  PANEL_HOST="$(validate_hostname "$PANEL_HOST")" \
    || die "Invalid panel hostname: $PANEL_HOST"

  if [[ -z "$ADMIN_CIDR" ]]; then
    detected="$(default_admin_cidr)"
    if [[ -n "$detected" ]]; then
      read -r -p "Administrative CIDR [$detected]: " ADMIN_CIDR </dev/tty
      ADMIN_CIDR="${ADMIN_CIDR:-$detected}"
    else
      read -r -p "Administrative CIDR (for example 192.0.2.10/32): " ADMIN_CIDR </dev/tty
    fi
  fi
  ADMIN_CIDR="$(validate_cidr "$ADMIN_CIDR")" \
    || die "Invalid administrative CIDR: $ADMIN_CIDR"

  case "$MTA" in postfix|exim) ;; *) die "MTA must be postfix or exim" ;; esac
  for role in "${ROLES[@]}"; do
    case "$role" in
      control|web|database|mail|dns|backup|edge) ;;
      *) die "Unsupported role: $role" ;;
    esac
  done
}

show_plan_and_confirm(){
  local reply=""
  printf '\nHostPanel installation plan\n'
  printf '  release commit: %s\n' "$REVIEWED_COMMIT_SHA"
  printf '  panel hostname: %s\n' "$PANEL_HOST"
  printf '  admin CIDR:     %s\n' "$ADMIN_CIDR"
  printf '  MTA:            %s\n' "$MTA"
  printf '  webserver:      nginx_apache\n'
  printf '  mode:           %s\n' "$([[ "$CHECK_ONLY" == yes ]] && printf check-only || { [[ "$REINSTALL" == yes ]] && printf reinstall || printf install; })"
  if ((${#ROLES[@]})); then
    printf '  roles:          %s\n' "${ROLES[*]}"
  else
    printf '  roles:          all\n'
  fi
  if [[ "$ASSUME_YES" == no && "$CHECK_ONLY" == no ]]; then
    read -r -p "Run preflight and install HostPanel? [y/N] " reply </dev/tty
    [[ "$reply" =~ ^[Yy]$ ]] || die "Installation cancelled"
  fi
}

download_reviewed_file(){
  local repository_path="$1" destination="$2"
  mkdir -p -- "$(dirname -- "$destination")"
  curl -q --proto '=https' --tlsv1.2 \
    --fail --location --silent --show-error \
    --retry 3 --retry-all-errors --connect-timeout 15 --max-time 180 \
    --config "$AUTH_FILE" \
    "${REPOSITORY_API}/contents/${repository_path}?ref=${REVIEWED_COMMIT_SHA}" \
    -o "$destination"
}

verify_custombuild_files(){
  local index path destination actual
  ((${#CUSTOMBUILD_PATHS[@]} == ${#CUSTOMBUILD_BLOBS[@]})) \
    || die "Internal CustomBuild manifest length mismatch"
  for index in "${!CUSTOMBUILD_PATHS[@]}"; do
    path="${CUSTOMBUILD_PATHS[$index]}"
    destination="$PRODUCT_DIR/$path"
    download_reviewed_file "$path" "$destination"
    actual="$(git hash-object --no-filters "$destination")" \
      || die "Could not hash reviewed CustomBuild input: $path"
    [[ "$actual" == "${CUSTOMBUILD_BLOBS[$index]}" ]] \
      || die "CustomBuild Git blob verification failed: $path"
  done
  python3 -m py_compile \
    "$PRODUCT_DIR/tools/hostpanel-build.py" \
    "$PRODUCT_DIR/tools/hostpanel_build_config.py" \
    "$PRODUCT_DIR/tools/hostpanel_build_packages.py" \
    "$PRODUCT_DIR/tools/hostpanel_build_operations.py" \
    "$PRODUCT_DIR/tools/hostpanel_build_cli.py" \
    "$PRODUCT_DIR/tools/hostpanel_build_ssl.py" \
    "$PRODUCT_DIR/tools/hostpanel_build_web.py" \
    "$PRODUCT_DIR/tools/patch_custombuild_runtime.py"
  bash -n "$PRODUCT_DIR/tools/install-hostpanel-build.sh"
}

ensure_prerequisites
prompt_inputs
show_plan_and_confirm
read_token

WORK_DIR="$(mktemp -d /tmp/hostpanel-quick-install.XXXXXX)" \
  || die "Could not create the private installer directory"
chmod 700 "$WORK_DIR"
PRODUCT_DIR="$WORK_DIR/product"
mkdir -m 700 "$PRODUCT_DIR"
AUTH_FILE="$WORK_DIR/github-curl.conf"
{
  printf 'header = "Accept: application/vnd.github.raw+json"\n'
  printf 'header = "Authorization: Bearer %s"\n' "$TOKEN"
  printf 'header = "X-GitHub-Api-Version: 2022-11-28"\n'
} >"$AUTH_FILE"
chmod 600 "$AUTH_FILE"

say "Downloading reviewed HostPanel and CustomBuild inputs"
download_reviewed_file bootstrap-install.sh "$WORK_DIR/bootstrap-install.sh"
download_reviewed_file tools/validate-production-vm.sh "$WORK_DIR/validate-production-vm.sh"
verify_custombuild_files

[[ "$(git hash-object --no-filters "$WORK_DIR/bootstrap-install.sh")" == "$BOOTSTRAP_BLOB" ]] \
  || die "Bootstrap Git blob verification failed"
[[ "$(git hash-object --no-filters "$WORK_DIR/validate-production-vm.sh")" == "$VALIDATOR_BLOB" ]] \
  || die "Validator Git blob verification failed"
bash -n "$WORK_DIR/bootstrap-install.sh"
bash -n "$WORK_DIR/validate-production-vm.sh"

GIT_AUTH_HEADER="$(printf 'x-access-token:%s' "$TOKEN" | base64 | tr -d '\r\n')"
export GIT_CONFIG_COUNT=1
export GIT_CONFIG_KEY_0='http.https://github.com/.extraheader'
export GIT_CONFIG_VALUE_0="AUTHORIZATION: basic $GIT_AUTH_HEADER"
export GIT_TERMINAL_PROMPT=0
TOKEN=""
GIT_AUTH_HEADER=""
rm -f -- "$AUTH_FILE"
AUTH_FILE=""

install_args=(--mta "$MTA")
[[ "$REINSTALL" == no ]] || install_args=(--reinstall "${install_args[@]}")
for role in "${ROLES[@]}"; do install_args+=(--role "$role"); done
common_env=(
  HP_REPO_REF="$REVIEWED_COMMIT_SHA"
  HP_PANEL_HOST="$PANEL_HOST"
  HP_PANEL_ADMIN_CIDR="$ADMIN_CIDR"
  HP_MULTI_PHP_REPO=off
  HP_RSPAMD_REPO=off
  HP_WEBSERVER_MODE=nginx_apache
)

say "Running HostPanel preflight"
env "${common_env[@]}" bash "$WORK_DIR/bootstrap-install.sh" --check "${install_args[@]}"

if [[ "$CHECK_ONLY" == yes ]]; then
  printf '\nPreflight passed. No packages or persistent installer files were changed.\n'
  exit 0
fi

install -o root -g root -m 0700 "$WORK_DIR/bootstrap-install.sh" /root/bootstrap-install.sh
install -o root -g root -m 0700 "$WORK_DIR/validate-production-vm.sh" /root/validate-production-vm.sh

say "Installing HostPanel with nginx and Apache"
env "${common_env[@]}" bash /root/bootstrap-install.sh "${install_args[@]}"

say "Installing the verified HostPanel CustomBuild tool"
HP_WEBSERVER_MODE=nginx_apache bash "$PRODUCT_DIR/tools/install-hostpanel-build.sh"

printf '\nHostPanel %s installed from reviewed commit %s.\n' \
  "$(cat /opt/hostpanel/VERSION 2>/dev/null || printf unknown)" \
  "$REVIEWED_COMMIT_SHA"
printf 'Base webserver: nginx_apache\n'
printf 'CustomBuild: sudo hostpanel-build options\n'
printf 'Installer log: /var/log/hostpanel-installer/install.log\n'
