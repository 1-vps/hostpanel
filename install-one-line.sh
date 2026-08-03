#!/usr/bin/env bash
# Minimal unattended HostPanel entry point.
# Usage: printf '%s' "$HOSTPANEL_GITHUB_TOKEN" | sudo env SSH_CONNECTION="${SSH_CONNECTION:-}" bash install-one-line.sh example.com
set -Eeuo pipefail

PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH
umask 077
unset PYTHONPATH PYTHONHOME BASH_ENV ENV LD_PRELOAD LD_LIBRARY_PATH

readonly AUTO_INSTALL_COMMIT="0722d75dd0dd2b64f730012700b5bd45a7ce0c41"
readonly AUTO_INSTALL_BLOB="984c23040306429858662a6b51a62e983e7f2796"
readonly REPOSITORY_API="https://api.github.com/repos/1-vps/hostpanel"

DOMAIN="${HP_PANEL_DOMAIN:-}"
TOKEN=""
WORK_DIR=""
AUTH_FILE=""

die(){ printf '\nError: %s\n' "$*" >&2; exit 1; }
say(){ printf '\n==> %s\n' "$*"; }

usage(){
  cat <<'HELP'
Usage:
  printf '%s' "$HOSTPANEL_GITHUB_TOKEN" | sudo env \
    SSH_CONNECTION="${SSH_CONNECTION:-}" \
    bash install-one-line.sh [DOMAIN]

The command never prompts. DOMAIN is optional when hostname -f already returns a
valid FQDN. DOMAIN is converted to panel.DOMAIN by the verified automatic
installer. The base webserver mode is nginx_apache; use hostpanel-build after
installation to select nginx, Apache, OpenLiteSpeed, or nginx_apache.

Optional environment:
  HP_PANEL_HOST, HP_PANEL_DOMAIN, HP_PANEL_ADMIN_CIDR
  HP_MTA=postfix|exim, HP_ROLES="control web database mail dns backup edge"
  HP_REINSTALL=yes, HP_CHECK_ONLY=yes, HP_POST_INSTALL_CHECK=no

The GitHub token must be supplied on standard input without a trailing newline.
It is used only through a private curl configuration and inherited descriptor.
HELP
}

cleanup(){
  local status=$?
  [[ -z "${AUTH_FILE:-}" ]] || rm -f -- "$AUTH_FILE"
  [[ -z "${WORK_DIR:-}" ]] || rm -rf -- "$WORK_DIR"
  TOKEN=""
  unset TOKEN
  return "$status"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

while (($#)); do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --domain)
      (($# >= 2)) || die "--domain requires a value"
      DOMAIN="$2"; shift 2 ;;
    --)
      shift; break ;;
    -*) die "Unknown option: $1" ;;
    *)
      [[ -z "$DOMAIN" ]] || die "Domain was supplied more than once"
      DOMAIN="$1"; shift ;;
  esac
done
(($# == 0)) || die "Unexpected positional arguments"

[[ "$EUID" -eq 0 ]] || die "Run this installer through sudo or as root"

read_token_from_stdin(){
  local character=""
  [[ ! -t 0 ]] || die "Pipe the GitHub token to standard input; interactive prompting is disabled"
  while IFS= read -r -N 1 character; do
    TOKEN+="$character"
    ((${#TOKEN} <= 512)) || die "GitHub token is longer than 512 characters"
  done
  [[ "$TOKEN" =~ ^[A-Za-z0-9_]{20,512}$ ]] \
    || die "GitHub token has an unsafe format"
}

ensure_prerequisites(){
  local missing=() command check_only="${HP_CHECK_ONLY:-no}"
  for command in curl git mktemp; do
    command -v "$command" >/dev/null 2>&1 || missing+=("$command")
  done
  ((${#missing[@]} == 0)) && return 0

  check_only="${check_only,,}"
  case "$check_only" in
    yes|1|true|on) die "Missing commands (${missing[*]}); check-only never installs packages" ;;
  esac

  say "Installing one-line bootstrap prerequisites"
  if command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get -o Acquire::Retries=3 update -qq
    apt-get -o Acquire::Retries=3 install -y -qq ca-certificates curl git coreutils
  elif command -v dnf >/dev/null 2>&1; then
    dnf -y --setopt=retries=3 install ca-certificates curl git coreutils
  else
    die "Missing commands (${missing[*]}) and no supported package manager was found"
  fi

  for command in curl git mktemp; do
    command -v "$command" >/dev/null 2>&1 || die "$command is still unavailable"
  done
}

read_token_from_stdin
ensure_prerequisites

WORK_DIR="$(mktemp -d /tmp/hostpanel-one-line.XXXXXX)" \
  || die "Could not create the private launcher directory"
chmod 0700 "$WORK_DIR"
AUTH_FILE="$WORK_DIR/github-curl.conf"
{
  printf 'header = "Accept: application/vnd.github.raw+json"\n'
  printf 'header = "Authorization: Bearer %s"\n' "$TOKEN"
  printf 'header = "X-GitHub-Api-Version: 2022-11-28"\n'
} >"$AUTH_FILE"
chmod 0600 "$AUTH_FILE"

say "Downloading the immutable automatic installer"
curl -q --proto '=https' --tlsv1.2 \
  --fail --location --silent --show-error \
  --retry 5 --retry-all-errors --connect-timeout 15 --max-time 180 \
  --config "$AUTH_FILE" \
  "${REPOSITORY_API}/contents/auto-install.sh?ref=${AUTO_INSTALL_COMMIT}" \
  -o "$WORK_DIR/auto-install.sh"

rm -f -- "$AUTH_FILE"
AUTH_FILE=""
[[ "$(git hash-object "$WORK_DIR/auto-install.sh")" == "$AUTO_INSTALL_BLOB" ]] \
  || die "Automatic installer Git blob verification failed"
bash -n "$WORK_DIR/auto-install.sh"
chmod 0700 "$WORK_DIR/auto-install.sh"

if [[ -n "$DOMAIN" && -z "${HP_PANEL_HOST:-}" ]]; then
  export HP_PANEL_DOMAIN="$DOMAIN"
fi

exec 3<<<"$TOKEN"
TOKEN=""
unset TOKEN

say "Starting verified unattended HostPanel installation"
HP_GITHUB_TOKEN_FD=3 bash "$WORK_DIR/auto-install.sh"
exec 3<&-
