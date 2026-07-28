#!/usr/bin/env bash
#
# HostPanel — local, reviewable installer
# Run from an extracted, signature-verified source release.
#
# Installs a role-selected HostPanel node on Ubuntu 22.04/24.04, Debian 12/13,
# Rocky Linux 9/10, or AlmaLinux 9/10.
# Run with --check or --dry-run before changing a server.
#
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"

PANEL_USER="hostpanel"
PANEL_DIR="/opt/hostpanel"
PANEL_PORT="${HP_PANEL_PORT:-2222}"
PANEL_BACKEND_PORT="${HP_PANEL_BACKEND_PORT:-12722}"
VHOST_ROOT="/home/vhosts"
MAIL_ROOT="/var/mail/vhosts"
BACKUP_DIR="/var/backups/hostpanel"
VMAIL_UID="5000"
REPO="${HP_REPO:-}"
REPO_REF="${HP_REPO_REF:-}"
LOG="/var/log/hostpanel-install.log"
UPDATE_MANIFEST="${HP_UPDATE_MANIFEST:-}"
PREVIOUS_MAIL_MTA=""
if [[ -r /etc/hostpanel/mail-mta ]]; then
  PREVIOUS_MAIL_MTA="$(tr -d '[:space:]' </etc/hostpanel/mail-mta)"
fi
[[ "$PREVIOUS_MAIL_MTA" =~ ^(postfix|exim)$ ]] || PREVIOUS_MAIL_MTA=""
MAIL_MTA="${HP_MAIL_MTA:-${PREVIOUS_MAIL_MTA:-postfix}}"

INSTALL_STARTED=no
INSTALL_COMPLETED=no
CURRENT_STAGE="startup"
SERVICE_WAS_ACTIVE=no
REINSTALL_SNAPSHOT=""
LOCK_DIR=""
BOOTSTRAP_TEMP_DIR=""
SOURCE_TEMP_DIR=""
RUNTIME_REPLACED=no
RUNTIME_DIR=""
RUNTIME_OLD_DIR=""
PREVIOUS_VENV_TARGET=""
TREE_ROLLBACK_DESTS=()
TREE_ROLLBACK_BACKUPS=()

state_write(){
  [[ "$INSTALL_STARTED" == yes ]] || return 0
  mkdir -p /etc/hostpanel
  local tmp="/etc/hostpanel/install-state.tmp.$$"
  {
    printf 'status=%s\n' "$1"
    printf 'mode=%s\n' "$([[ "$REINSTALL" == yes ]] && echo reinstall || echo install)"
    printf 'stage=%s\n' "${2:-$CURRENT_STAGE}"
    printf 'updated=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    [[ -n "$REINSTALL_SNAPSHOT" ]] && printf 'snapshot=%s\n' "$REINSTALL_SNAPSHOT"
  } >"$tmp"
  chmod 600 "$tmp"
  mv -f "$tmp" /etc/hostpanel/install-state
}

say(){
  CURRENT_STAGE="$*"
  state_write in-progress "$CURRENT_STAGE"
  printf "\n\033[1;36m==>\033[0m %s\n" "$*"
}
ok(){  printf "\033[1;32m  ok\033[0m %s\n" "$*"; }
warn(){ printf "\033[1;33m  warning\033[0m %s\n" "$*" >&2; }
die(){ printf "\n\033[1;31mError:\033[0m %s\n" "$*" >&2; exit 1; }
random_alnum(){ python3 - "$1" <<'PYRAND'
import secrets,string,sys
n=int(sys.argv[1]); alphabet=string.ascii_letters+string.digits
print("".join(secrets.choice(alphabet) for _ in range(n)))
PYRAND
}

ensure_bootstrap_temp_dir(){
  if [[ -z "$BOOTSTRAP_TEMP_DIR" ]]; then
    BOOTSTRAP_TEMP_DIR="$(mktemp -d /tmp/hostpanel-bootstrap.XXXXXX)" \
      || die "Could not create a private bootstrap directory"
    chmod 700 "$BOOTSTRAP_TEMP_DIR"
  fi
}

config_value(){
  local key="$1" file="${2:-$PANEL_DIR/config.env}"
  [[ -r "$file" ]] || return 1
  awk -v key="$key" 'index($0, key "=") == 1 {sub(/^[^=]*=/, ""); print; exit}' "$file"
}

create_reinstall_snapshot(){
  local stamp item
  local -a items=()
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  mkdir -p "$BACKUP_DIR/install"
  REINSTALL_SNAPSHOT="$BACKUP_DIR/install/reinstall-$stamp.tar.gz"
  for item in \
    opt/hostpanel/app opt/hostpanel/ops opt/hostpanel/sdk opt/hostpanel/tools \
    opt/hostpanel/integrations opt/hostpanel/releases opt/hostpanel/VERSION \
    opt/hostpanel/requirements.lock opt/hostpanel/config.env \
    opt/hostpanel/credentials opt/hostpanel/hostpanel.db opt/hostpanel/tls \
    etc/hostpanel etc/systemd/system/hostpanel.service \
    etc/sudoers.d/hostpanel root/.my.cnf; do
    [[ -e "/$item" ]] && items+=("$item")
  done
  while IFS= read -r item; do
    item="${item#/}"
    [[ -n "$item" ]] && items+=("$item")
  done < <(
    find /etc/cron.d /etc/nginx /etc/apache2 /etc/httpd \
      /usr/local/lsws/conf/vhosts -maxdepth 3 -name '*hostpanel*' \
      -print 2>/dev/null || true
  )
  if ((${#items[@]})); then
    tar -C / -czf "$REINSTALL_SNAPSHOT" --ignore-failed-read \
      "${items[@]}" >>"$LOG" 2>&1 \
      || die "Could not create the reinstall safety snapshot"
  else
    tar -C / -czf "$REINSTALL_SNAPSHOT" --files-from /dev/null \
      >>"$LOG" 2>&1 || die "Could not create the reinstall safety snapshot"
  fi
  chmod 600 "$REINSTALL_SNAPSHOT"
  printf '%s\n' "$REINSTALL_SNAPSHOT" >/etc/hostpanel/last-reinstall-snapshot
  chmod 600 /etc/hostpanel/last-reinstall-snapshot
  state_write in-progress "reinstall snapshot created"
  ok "reinstall safety snapshot: $REINSTALL_SNAPSHOT"
}

installer_exit(){
  local rc=$? i destination backup
  trap - EXIT
  if [[ "$INSTALL_STARTED" == yes ]]; then
    if [[ "$rc" -eq 0 && "$INSTALL_COMPLETED" == yes ]]; then
      state_write complete "completed"
    else
      if [[ "$RUNTIME_REPLACED" == yes && -n "$RUNTIME_DIR" ]]; then
        rm -rf "$RUNTIME_DIR"
        if [[ -n "$RUNTIME_OLD_DIR" && -d "$RUNTIME_OLD_DIR" ]]; then
          mv "$RUNTIME_OLD_DIR" "$RUNTIME_DIR" || true
          ln -sfn "venvs/$(basename "$RUNTIME_DIR")" "$PANEL_DIR/venv" || true
        elif [[ -n "$PREVIOUS_VENV_TARGET" ]]; then
          ln -sfn "$PREVIOUS_VENV_TARGET" "$PANEL_DIR/venv" || true
        else
          rm -f "$PANEL_DIR/venv"
        fi
      fi
      for ((i=${#TREE_ROLLBACK_DESTS[@]}-1; i>=0; i--)); do
        destination="${TREE_ROLLBACK_DESTS[$i]}"
        backup="${TREE_ROLLBACK_BACKUPS[$i]}"
        rm -rf "$destination"
        [[ -z "$backup" || ! -e "$backup" ]] || mv "$backup" "$destination" || true
      done
      if [[ "$REINSTALL" == yes && -s "$REINSTALL_SNAPSHOT" ]]; then
        tar -C / -xzf "$REINSTALL_SNAPSHOT" >>"$LOG" 2>&1 || true
        command -v systemctl >/dev/null 2>&1 && systemctl daemon-reload >>"$LOG" 2>&1 || true
      fi
      state_write failed "$CURRENT_STAGE"
      if [[ "$REINSTALL" == yes && "$SERVICE_WAS_ACTIVE" == yes ]] \
         && command -v systemctl >/dev/null 2>&1 \
         && ! systemctl is-active --quiet hostpanel 2>/dev/null; then
        systemctl start hostpanel >>"$LOG" 2>&1 || true
      fi
    fi
  fi
  [[ -z "$BOOTSTRAP_TEMP_DIR" ]] || rm -rf -- "$BOOTSTRAP_TEMP_DIR"
  [[ -z "$SOURCE_TEMP_DIR" ]] || rm -rf -- "$SOURCE_TEMP_DIR"
  [[ -z "$LOCK_DIR" ]] || rmdir "$LOCK_DIR" 2>/dev/null || true
  exit "$rc"
}
trap installer_exit EXIT

CHECK_ONLY=no
DRY_RUN=no
REINSTALL=no
ROLE_ARGS=()
while (($#)); do
  case "$1" in
    --check) CHECK_ONLY=yes ;;
    --dry-run) DRY_RUN=yes ;;
    --reinstall) REINSTALL=yes ;;
    --role) shift; [[ $# -gt 0 ]] || die "--role requires a value"; ROLE_ARGS+=("$1") ;;
    --role=*) ROLE_ARGS+=("${1#*=}") ;;
    --mta) shift; [[ $# -gt 0 ]] || die "--mta requires postfix or exim"; MAIL_MTA="$1" ;;
    --mta=*) MAIL_MTA="${1#*=}" ;;
    --help|-h)
      cat <<'EOF'
Usage: install.sh [--check] [--dry-run] [--reinstall] [--role ROLE[,ROLE...]] [--mta postfix|exim]
Roles: control, web, database, mail, dns, backup, edge
--reinstall repairs an interrupted or existing installation while preserving data,
credentials, the existing administrator, database URLs, and unmanaged config keys.
Mail MTA defaults to Postfix. Exim is installed with native DKIM signing and Dovecot authentication.
FTP is disabled by default; set HP_ENABLE_FTP=yes only when it is required.
EOF
      exit 0 ;;
    *) die "Unknown argument: $1" ;;
  esac
  shift
done

# Reinstall mode reads only simple KEY=value fields; it never sources the old
# configuration as shell code. Explicit environment variables still win.
REINSTALL_PANEL_HOST=""
if [[ "$REINSTALL" == yes && -r "$PANEL_DIR/config.env" ]]; then
  if [[ -z "${HP_PANEL_PORT+x}" ]]; then
    old_value="$(config_value HP_PORT || true)"
    [[ -z "$old_value" ]] || PANEL_PORT="$old_value"
  fi
  if [[ -z "${HP_PANEL_BACKEND_PORT+x}" ]]; then
    old_value="$(config_value HP_PANEL_BACKEND_PORT || true)"
    [[ -z "$old_value" ]] || PANEL_BACKEND_PORT="$old_value"
  fi
  if [[ -z "${HP_UPDATE_MANIFEST+x}" ]]; then
    old_value="$(config_value HP_UPDATE_MANIFEST || true)"
    [[ -z "$old_value" ]] || UPDATE_MANIFEST="$old_value"
  fi
  if [[ -z "${HP_PANEL_HOST+x}" ]]; then
    REINSTALL_PANEL_HOST="$(config_value HP_TRUSTED_HOSTS || true)"
    REINSTALL_PANEL_HOST="${REINSTALL_PANEL_HOST%%,*}"
  fi
fi

MAIL_MTA="${MAIL_MTA,,}"
[[ "$MAIL_MTA" =~ ^(postfix|exim)$ ]] || die "Unsupported MTA: $MAIL_MTA (choose postfix or exim)"
[[ "$PANEL_PORT" =~ ^[0-9]+$ && "$PANEL_BACKEND_PORT" =~ ^[0-9]+$ ]] \
  || die "HP_PANEL_PORT and HP_PANEL_BACKEND_PORT must be numeric"
((PANEL_PORT >= 1 && PANEL_PORT <= 65535 && PANEL_BACKEND_PORT >= 1024 && PANEL_BACKEND_PORT <= 65535)) \
  || die "Panel ports are out of range"
((PANEL_PORT != PANEL_BACKEND_PORT && PANEL_PORT != 80 && PANEL_PORT != 443)) \
  || die "Panel public/backend ports collide with each other or HTTP/HTTPS"

VALID_ROLES=(control web database mail dns backup edge)
if ((${#ROLE_ARGS[@]} == 0)); then
  if [[ "$REINSTALL" == yes && -r /etc/hostpanel/roles.conf ]]; then
    previous_roles="$(sed -n 's/^roles=//p' /etc/hostpanel/roles.conf | head -1)"
    if [[ -n "$previous_roles" ]]; then
      ROLE_ARGS=("$previous_roles")
    else
      ROLE_ARGS=(control web database mail dns backup edge)
    fi
  else
    ROLE_ARGS=(control web database mail dns backup edge)
  fi
fi
ROLES=()
for raw in "${ROLE_ARGS[@]}"; do
  IFS=',' read -r -a split <<<"$raw"
  for role in "${split[@]}"; do
    [[ " ${VALID_ROLES[*]} " == *" $role "* ]] || die "Unsupported role: $role"
    [[ " ${ROLES[*]} " == *" $role "* ]] || ROLES+=("$role")
  done
done
has_role(){ [[ " ${ROLES[*]} " == *" $1 "* ]]; }
ROLE_CSV="$(IFS=,; echo "${ROLES[*]}")"

say "Checking operating system, capacity and selected roles"
OS_RELEASE_FILE="${HP_OS_RELEASE_FILE:-/etc/os-release}"
[[ -r "$OS_RELEASE_FILE" ]] || die "Cannot read $OS_RELEASE_FILE"
# shellcheck disable=SC1090
. "$OS_RELEASE_FILE"
ID="${ID,,}"
EL_MAJOR="${VERSION_ID%%.*}"
SUPPORTED=no
[[ "$ID" == ubuntu && ("$VERSION_ID" == 22.04 || "$VERSION_ID" == 24.04) ]] && SUPPORTED=yes
[[ "$ID" == debian && ("$VERSION_ID" == 12 || "$VERSION_ID" == 13) ]] && SUPPORTED=yes
[[ ("$ID" == rocky || "$ID" == almalinux) && ("$EL_MAJOR" == 9 || "$EL_MAJOR" == 10) ]] && SUPPORTED=yes

case " $ID ${ID_LIKE:-} " in
  *" rhel "*|*" fedora "*|*" centos "*|*" rocky "*|*" almalinux "*) PKG_FAMILY=rhel ;;
  *) PKG_FAMILY=debian ;;
esac
[[ "$ID" == rocky || "$ID" == almalinux ]] && PKG_FAMILY=rhel

if [[ "$SUPPORTED" != yes && "${HP_ALLOW_UNTESTED_OS:-no}" != yes ]]; then
  die "Unsupported OS release: ${PRETTY_NAME:-$ID $VERSION_ID}. Supported: Ubuntu 22.04/24.04, Debian 12/13, Rocky Linux 9/10 and AlmaLinux 9/10."
fi
case "$(command -v dpkg >/dev/null 2>&1 && dpkg --print-architecture 2>/dev/null || uname -m)" in
  amd64|arm64|x86_64|aarch64) ;;
  *) die "Unsupported processor architecture" ;;
esac
MEM_KB="$(awk '/MemTotal/{print $2}' /proc/meminfo)"
((MEM_KB >= 1900000)) || die "At least 2 GB RAM is required"
FREE_KB="$(df -Pk / | awk 'NR==2{print $4}')"
((FREE_KB >= 10*1024*1024)) || die "At least 10 GB free disk space is required"
PREFLIGHT_HOST="${HP_PANEL_HOST:-${REINSTALL_PANEL_HOST:-$(hostname -f 2>/dev/null || true)}}"
[[ "$PREFLIGHT_HOST" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,}$ ]] \
  || die "Configure a valid FQDN or set HP_PANEL_HOST before installation"

if [[ "$REINSTALL" != yes && -f "$PANEL_DIR/config.env" ]]; then
  die "An existing HostPanel installation was detected. Run this reviewed release with --reinstall to preserve and repair it."
fi
if [[ "$REINSTALL" != yes && ! -d "$PANEL_DIR" && "${HP_ALLOW_EXISTING_STACK:-no}" != yes ]] && command -v ss >/dev/null; then
  PORTS=($PANEL_PORT $PANEL_BACKEND_PORT)
  if has_role web || has_role edge; then PORTS+=(80 443); fi
  if has_role mail; then PORTS+=(25 587 993); fi
  if has_role dns; then PORTS+=(53); fi
  for port in "${PORTS[@]}"; do
    if ss -ltnup 2>/dev/null | grep -Eq "[:.]${port}[[:space:]]"; then
      die "Port $port is already in use. Use a clean server or set HP_ALLOW_EXISTING_STACK=yes after reviewing conflicts."
    fi
  done
fi
ok "$PRETTY_NAME; roles: $ROLE_CSV; mail MTA: $MAIL_MTA"

if [[ "$CHECK_ONLY" == yes || "$DRY_RUN" == yes ]]; then
  printf '\nPreflight passed. No changes were made.\n'
  exit 0
fi
[[ $EUID -eq 0 ]] || die "Run as root: sudo bash install.sh"

mkdir -p /run/lock /etc/hostpanel
if command -v flock >/dev/null 2>&1; then
  exec 9>/run/lock/hostpanel-install.lock
  flock -n 9 || die "Another HostPanel installer is already running"
else
  LOCK_DIR=/run/lock/hostpanel-install.lock.d
  mkdir "$LOCK_DIR" 2>/dev/null || die "Another HostPanel installer is already running"
fi
[[ "$PKG_FAMILY" == debian ]] && export DEBIAN_FRONTEND=noninteractive
touch "$LOG"; chmod 600 "$LOG"
INSTALL_STARTED=yes
state_write in-progress "initialising"

if [[ "$REINSTALL" == yes ]]; then
  say "Preparing safe reinstall"
  create_reinstall_snapshot
  if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet hostpanel 2>/dev/null; then
    SERVICE_WAS_ACTIVE=yes
    systemctl stop hostpanel >>"$LOG" 2>&1 || die "Could not stop the existing HostPanel service"
    ok "existing HostPanel service stopped for code replacement"
  fi
fi

# Switching between MTAs cannot transfer queued messages. Refuse the change
# until the old queue is empty unless the operator explicitly accepts that
# risk, and retain a root-only configuration snapshot before package changes.
if has_role mail && [[ -n "$PREVIOUS_MAIL_MTA" && "$PREVIOUS_MAIL_MTA" != "$MAIL_MTA" ]]; then
  OLD_QUEUE_COUNT=0
  if [[ "$PREVIOUS_MAIL_MTA" == postfix && -x /usr/sbin/postqueue ]]; then
    OLD_QUEUE_COUNT="$(/usr/sbin/postqueue -j 2>/dev/null | grep -c . || true)"
  elif [[ "$PREVIOUS_MAIL_MTA" == exim ]]; then
    OLD_EXIM_BIN="$(command -v exim4 2>/dev/null || command -v exim 2>/dev/null || true)"
    [[ -n "$OLD_EXIM_BIN" ]] && OLD_QUEUE_COUNT="$("$OLD_EXIM_BIN" -bpc 2>/dev/null | tr -dc '0-9' || true)"
  fi
  OLD_QUEUE_COUNT="${OLD_QUEUE_COUNT:-0}"
  if ((OLD_QUEUE_COUNT > 0)) && [[ "${HP_ALLOW_MTA_SWITCH_WITH_QUEUE:-no}" != yes ]]; then
    die "The $PREVIOUS_MAIL_MTA queue contains $OLD_QUEUE_COUNT message(s). Drain it before switching to $MAIL_MTA, or set HP_ALLOW_MTA_SWITCH_WITH_QUEUE=yes after accepting message loss."
  fi
  MTA_SWITCH_BACKUP="$BACKUP_DIR/install/mta-switch-$(date -u +%Y%m%dT%H%M%SZ)-${PREVIOUS_MAIL_MTA}-to-${MAIL_MTA}.tar.gz"
  mkdir -p "$BACKUP_DIR/install"
  tar -czf "$MTA_SWITCH_BACKUP" --ignore-failed-read     /etc/postfix /etc/exim4 /etc/exim /etc/dovecot /etc/rspamd     /etc/hostpanel/mail-mta /etc/hostpanel/mail-limits.conf 2>>"$LOG" || true
  chmod 600 "$MTA_SWITCH_BACKUP" 2>/dev/null || true
  ok "mail configuration snapshot saved before $PREVIOUS_MAIL_MTA -> $MAIL_MTA switch"
fi

# --------------------------------------------------------------------------- #
say "Installing packages for roles: $ROLE_CSV"
if [[ "$PKG_FAMILY" == debian ]] && command -v debconf-set-selections >/dev/null 2>&1; then
  if has_role mail && [[ "$MAIL_MTA" == postfix ]]; then
    echo "postfix postfix/main_mailer_type select Internet Site" | debconf-set-selections
    echo "postfix postfix/mailname string $PREFLIGHT_HOST" | debconf-set-selections
  fi
  if has_role mail && [[ "$MAIL_MTA" == exim ]]; then
    echo "exim4-config exim4/dc_eximconfig_configtype select internet site; mail is sent and received directly using SMTP" | debconf-set-selections
    echo "exim4-config exim4/mailname string $PREFLIGHT_HOST" | debconf-set-selections
  fi
fi
# --------------------------------------------------------------------------- #
# Package layer.
#
# Every install goes through these three functions rather than calling apt-get
# directly, so adding a distribution family is a matter of filling in the dnf
# branch instead of finding eight call sites. The role model above is unchanged:
# roles still decide *what* is installed, this decides *how*.
#
pkg_failure(){
  local action="$1"
  printf '\nPackage-manager failure while %s.\n' "$action" >&2
  printf 'Last log lines from %s:\n' "$LOG" >&2
  tail -n 50 "$LOG" >&2 2>/dev/null || true
  if [[ "$PKG_FAMILY" == debian ]]; then
    printf '%s\n' \
      'Check DNS/network access, /etc/apt/sources.list*, proxy settings, and whether another apt/dpkg process holds the lock.' \
      'After correcting the cause, run: dpkg --configure -a && apt-get -f install' >&2
  else
    printf '%s\n' \
      'Check DNS/network access, enabled repositories, proxy settings, and whether another dnf process holds the lock.' \
      'After correcting the cause, run: dnf clean all && dnf makecache' >&2
  fi
  return 1
}

pkg_refresh(){
  local attempt
  for attempt in 1 2 3; do
    case "$PKG_FAMILY" in
      debian)
        if apt-get -o DPkg::Lock::Timeout=120 -o Acquire::Retries=3 update -qq >>"$LOG" 2>&1; then
          return 0
        fi
        ;;
      rhel)
        if dnf -q --setopt=timeout=60 --setopt=retries=5 makecache >>"$LOG" 2>&1; then
          return 0
        fi
        ;;
    esac
    if ((attempt < 3)); then sleep "$attempt"; fi
  done
  pkg_failure "refreshing repository metadata"
}

# Try a package transaction without printing a terminal failure. This is used
# for capability-selected optional packages where a repository may publish a
# different extension subset for each distribution and architecture.
pkg_try_install(){
  (($#)) || return 0
  local attempt packages=("$@")
  for attempt in 1 2 3; do
    case "$PKG_FAMILY" in
      debian)
        if apt-get -o DPkg::Lock::Timeout=120 -o Acquire::Retries=3 install -y -qq "${packages[@]}" >>"$LOG" 2>&1; then
          return 0
        fi
        ;;
      rhel)
        if dnf -y -q --setopt=timeout=60 --setopt=retries=5 install "${packages[@]}" >>"$LOG" 2>&1; then
          return 0
        fi
        ;;
    esac
    if ((attempt < 3)); then sleep "$attempt"; fi
  done
  return 1
}

# Required package transactions retain the detailed terminal/log failure.
pkg_install(){
  (($#)) || return 0
  local packages=("$@")
  pkg_try_install "${packages[@]}" && return 0
  pkg_failure "installing: ${packages[*]}"
}

pkg_available(){
  local package="$1" candidate
  case "$PKG_FAMILY" in
    debian)
      # `apt-cache show` can display metadata for an obsolete package whose
      # Candidate is `(none)`. APT cannot install it, so test policy instead.
      candidate="$(apt-cache policy "$package" 2>/dev/null | awk '/^[[:space:]]*Candidate:/ {print $2; exit}')"
      [[ -n "$candidate" && "$candidate" != "(none)" ]]
      ;;
    rhel)
      dnf -q list --available "$package" >/dev/null 2>&1 \
        || dnf -q list --installed "$package" >/dev/null 2>&1
      ;;
  esac
}

pkg_install_available(){
  local packages=() package
  for package in "$@"; do
    pkg_available "$package" && packages+=("$package")
  done
  if ((${#packages[@]})); then
    pkg_install "${packages[@]}"
  fi
}

pkg_manager_ready(){
  case "$PKG_FAMILY" in
    debian)
      command -v apt-get >/dev/null 2>&1 \
        || die "apt-get is unavailable on this Debian-family host"
      ;;
    rhel)
      command -v dnf >/dev/null 2>&1 \
        || die "dnf is unavailable on this RHEL-family host"
      ;;
  esac
}

pkg_repair(){
  case "$PKG_FAMILY" in
    debian)
      dpkg --configure -a >>"$LOG" 2>&1 \
        || { pkg_failure "configuring interrupted dpkg packages"; return 1; }
      apt-get -o DPkg::Lock::Timeout=120 -o Acquire::Retries=3 \
        -f install -y -qq >>"$LOG" 2>&1 \
        || { pkg_failure "repairing APT dependencies"; return 1; }
      ;;
    rhel)
      dnf -q --setopt=timeout=60 --setopt=retries=5 check \
        >>"$LOG" 2>&1 \
        || { pkg_failure "checking DNF package consistency"; return 1; }
      ;;
  esac
}

# Translate a Debian package name to this family's equivalent. Names that are
# identical across families are absent on purpose -- the table only carries
# genuine differences, so it stays short enough to audit.
pkg_name(){
  local name="$1"
  if [[ "$PKG_FAMILY" == debian ]]; then
    # Use concrete Debian package names rather than historical or virtual
    # aliases.  Debian 13 exposes dnsutils only as a virtual package,
    # renamed bind9utils to bind9-utils, and ships the former contrib
    # extensions inside postgresql-17 instead of postgresql-contrib.
    case "$name" in
      dnsutils)             printf 'bind9-dnsutils' ;;
      bind9utils)           printf 'bind9-utils' ;;
      postgresql-contrib)
        case "$ID:$VERSION_ID" in
          debian:13|debian:13.*) printf 'postgresql' ;;
          *)                     printf 'postgresql-contrib' ;;
        esac
        ;;
      *)                    printf '%s' "$name" ;;
    esac
    return
  fi
  case "$name" in
    apache2)                printf 'httpd' ;;
    apache2-utils)          printf 'httpd-tools' ;;
    libapache2-mod-fcgid)   printf 'mod_fcgid' ;;
    bind9)                  printf 'bind' ;;
    bind9utils)             printf 'bind-utils' ;;
    redis-server)           printf 'redis' ;;
    clamav-daemon)          printf 'clamd' ;;
    dovecot-core|dovecot-imapd|dovecot-lmtpd|dovecot-sieve|dovecot-managesieved)
                            printf 'dovecot' ;;
    exim4-base|exim4-config|exim4-daemon-heavy) printf 'exim' ;;
    postfix-pcre)           printf 'postfix' ;;
    python3-venv|python3-pip) printf 'python3-pip' ;;
    ufw)                    printf 'firewalld' ;;
    quotatool)              printf 'quota' ;;
    prometheus-node-exporter) printf 'node_exporter' ;;
    software-properties-common) printf 'dnf-plugins-core' ;;
    gnupg)                  printf 'gnupg2' ;;
    dnsutils)               printf 'bind-utils' ;;
    needrestart)            printf 'dnf-utils' ;;
    inotify-tools)          printf 'inotify-tools' ;;
    libfcgi-bin)            printf 'fcgi' ;;
    libnginx-mod-http-modsecurity) printf 'nginx-mod-modsecurity' ;;
    modsecurity-crs)        printf 'mod_security_crs' ;;
    db-util)                printf 'libdb-utils' ;;
    rclone)                 printf 'rclone' ;;
    roundcube|roundcube-core|roundcube-mysql) printf 'roundcubemail' ;;
    mariadb-server)         printf 'mariadb-server' ;;
    postgresql-contrib)     printf 'postgresql-contrib' ;;
    opendkim-tools)         printf 'opendkim-tools' ;;
    postsrsd)               printf 'postsrsd' ;;
    python3-certbot-nginx)  printf 'python3-certbot-nginx' ;;
    *)                      printf '%s' "$name" ;;
  esac
}

# Map a whole list through pkg_name, dropping duplicates the mapping creates
# (five dovecot packages collapse to one on RHEL).
pkg_map(){
  local out=() seen=" " mapped
  for item in "$@"; do
    mapped="$(pkg_name "$item")"
    [[ "$seen" == *" $mapped "* ]] && continue
    seen+="$mapped "
    out+=("$mapped")
  done
  printf '%s\n' "${out[@]}"
}

pkg_manager_ready
if [[ "$REINSTALL" == yes ]]; then
  say "Repairing package-manager state"
  pkg_repair || die "Could not repair the package-manager state; review $LOG"
  ok "package-manager state is consistent"
fi
pkg_refresh || die "Could not refresh package-manager metadata; review $LOG"
if [[ "$PKG_FAMILY" == debian ]]; then
  BOOTSTRAP_PACKAGES=(ca-certificates curl gnupg)
  if [[ "$ID" == ubuntu ]] && has_role web && [[ "${HP_MULTI_PHP_REPO:-auto}" != off ]]; then
    BOOTSTRAP_PACKAGES+=(software-properties-common)
  fi
  OPTIONAL_BOOTSTRAP_PACKAGES=(lsb-release)
else
  BOOTSTRAP_PACKAGES=(ca-certificates curl gnupg dnf-plugins-core)
  OPTIONAL_BOOTSTRAP_PACKAGES=()
fi
readarray -t BOOTSTRAP_PACKAGES < <(pkg_map "${BOOTSTRAP_PACKAGES[@]}")
pkg_install "${BOOTSTRAP_PACKAGES[@]}" \
  || die "Could not install required package-manager prerequisites; review $LOG"
if ((${#OPTIONAL_BOOTSTRAP_PACKAGES[@]})); then
  readarray -t OPTIONAL_BOOTSTRAP_PACKAGES < <(pkg_map "${OPTIONAL_BOOTSTRAP_PACKAGES[@]}")
  pkg_install_available "${OPTIONAL_BOOTSTRAP_PACKAGES[@]}" \
    || warn "Optional package-manager helpers could not be installed; continuing"
fi
command -v curl >/dev/null 2>&1 || die "curl is unavailable after bootstrap"
if [[ "$PKG_FAMILY" == debian ]]; then
  command -v gpg >/dev/null 2>&1 || die "gpg is unavailable after bootstrap"
fi
if [[ "$PKG_FAMILY" == rhel ]]; then
  dnf config-manager --help >/dev/null 2>&1 \
    || die "dnf config-manager is unavailable after installing dnf-plugins-core"
fi
if [[ "$PKG_FAMILY" == debian && "$ID" == ubuntu ]] && has_role web \
   && [[ "${HP_MULTI_PHP_REPO:-auto}" != off ]]; then
  command -v add-apt-repository >/dev/null 2>&1 \
    || die "add-apt-repository is unavailable after installing software-properties-common"
fi
DIST_CODENAME="${VERSION_CODENAME:-${UBUNTU_CODENAME:-}}"
if [[ "$PKG_FAMILY" == debian ]] \
   && { { has_role mail && [[ "${HP_RSPAMD_REPO:-auto}" != off ]]; } \
        || { has_role web && [[ "${HP_MULTI_PHP_REPO:-auto}" != off ]]; }; } \
   && [[ -z "$DIST_CODENAME" ]]; then
  die "Distribution codename is missing from $OS_RELEASE_FILE; set VERSION_CODENAME there or disable external repositories"
fi

# --------------------------------------------------------------------------- #
# Platform layer.
#
# Everything below the package layer that differs between families: unit names,
# where a vhost lives, which user the web server runs as, and which firewall is
# in front. Same reasoning as pkg_name -- one table that can be audited, rather
# than the same conditional written out thirty times.
if [[ "$PKG_FAMILY" == rhel ]]; then
  WEB_USER="apache"
  NGINX_CONF_DIR="/etc/nginx/conf.d";  NGINX_LINK_DIR="/etc/nginx/conf.d"
  APACHE_CONF_DIR="/etc/httpd/conf.d"; APACHE_PORTS="/etc/httpd/conf.d/hostpanel-ports.conf"
  APACHE_CTL="/usr/sbin/apachectl";    ZONE_DIR="/var/named/hostpanel"; ZONE_OWNER="named:named"; NAMED_MANAGED_CONF="/etc/named/hostpanel-zones.conf"
  VHOST_EXT=".conf"
  FW_TOOL="firewalld"
else
  WEB_USER="www-data"
  NGINX_CONF_DIR="/etc/nginx/sites-available"; NGINX_LINK_DIR="/etc/nginx/sites-enabled"
  APACHE_CONF_DIR="/etc/apache2";              APACHE_PORTS="/etc/apache2/ports.conf"
  APACHE_CTL="/usr/sbin/apache2ctl";           ZONE_DIR="/etc/bind/zones"; ZONE_OWNER="bind:bind"; NAMED_MANAGED_CONF="/etc/bind/named.conf.local"
  VHOST_EXT=""
  FW_TOOL="ufw"
fi

# Logical service name -> unit name on this family.
svc(){
  if [[ "$PKG_FAMILY" != rhel ]]; then
    case "$1" in
      apache) printf 'apache2' ;; dns) printf 'named' ;; redis) printf 'redis-server' ;;
      exim) printf 'exim4' ;; clamav) printf 'clamav-daemon' ;; *) printf '%s' "$1" ;;
    esac
    return
  fi
  case "$1" in
    apache) printf 'httpd' ;; dns) printf 'named' ;; redis) printf 'redis' ;;
    exim) printf 'exim' ;; clamav) printf 'clamd' ;; *) printf '%s' "$1" ;;
  esac
}


php_tag(){ printf '%s' "${1//./}"; }
php_service(){ if [[ "$PKG_FAMILY" == rhel ]]; then printf 'php%s-php-fpm' "$(php_tag "$1")"; else printf 'php%s-fpm' "$1"; fi; }
php_fpm_bin(){ if [[ "$PKG_FAMILY" == rhel ]]; then printf '/opt/remi/php%s/root/usr/sbin/php-fpm' "$(php_tag "$1")"; else printf '/usr/sbin/php-fpm%s' "$1"; fi; }
php_cli_bin(){ if [[ "$PKG_FAMILY" == rhel ]]; then printf '/opt/remi/php%s/root/usr/bin/php' "$(php_tag "$1")"; else printf '/usr/bin/php%s' "$1"; fi; }
php_pool_dir(){ if [[ "$PKG_FAMILY" == rhel ]]; then printf '/etc/opt/remi/php%s/php-fpm.d' "$(php_tag "$1")"; else printf '/etc/php/%s/fpm/pool.d' "$1"; fi; }
php_ini_dir(){ if [[ "$PKG_FAMILY" == rhel ]]; then printf '/etc/opt/remi/php%s/php.d' "$(php_tag "$1")"; else printf '/etc/php/%s/mods-available' "$1"; fi; }
php_package_candidates(){
  local version="$1" suffix="$2" tag base
  if [[ "$PKG_FAMILY" == debian ]]; then printf 'php%s-%s\n' "$version" "$suffix"; return; fi
  tag="$(php_tag "$version")"; base="php${tag}-php-"
  case "$suffix" in
    mysql) printf '%smysqlnd\n%smysql\n' "$base" "$base" ;;
    sqlite3) printf '%spdo\n%ssqlite3\n' "$base" "$base" ;;
    zip) printf '%specl-zip\n%szip\n' "$base" "$base" ;;
    redis) printf '%specl-redis6\n%specl-redis5\n%specl-redis\n' "$base" "$base" "$base" ;;
    memcached|apcu|igbinary|msgpack|ssh2|yaml|mailparse|protobuf|lz4|zstd|oauth|amqp|zmq|mongodb|grpc|swoole|ds|uuid|xdebug|pcov) printf '%specl-%s\n' "$base" "$suffix" ;;
    imagick) printf '%specl-imagick-im7\n%specl-imagick\n' "$base" "$base" ;;
    dev) printf '%sdevel\n' "$base" ;;
    readline) printf '%sprocess\n%scommon\n' "$base" "$base" ;;
    curl|bz2) printf '%scommon\n' "$base" ;;
    *) printf '%s%s\n' "$base" "$suffix" ;;
  esac
}
php_package_for(){
  local candidate
  while IFS= read -r candidate; do
    [[ -n "$candidate" ]] || continue
    if pkg_available "$candidate"; then printf '%s' "$candidate"; return 0; fi
  done < <(php_package_candidates "$1" "$2")
  return 1
}

# ---- Firewall ------------------------------------------------------------
# The installer only ever opens ports and never resets policy, so three verbs
# are enough. firewalld works in permanent rules plus a reload, ufw applies
# immediately -- fw_commit hides that difference.
fw_snapshot(){
  if [[ "$FW_TOOL" == firewalld ]]; then
    firewall-cmd --list-all >"$1" 2>&1 || true
  else
    ufw status verbose >"$1" 2>&1 || true
  fi
}
fw_is_active(){
  if [[ "$FW_TOOL" == firewalld ]]; then
    firewall-cmd --state 2>/dev/null | grep -q running
  else
    ufw status 2>/dev/null | grep -q "Status: active"
  fi
}
fw_default_deny(){
  [[ "$FW_TOOL" == firewalld ]] && return 0   # the default zone already does this
  ufw default deny incoming  >>"$LOG" 2>&1
  ufw default allow outgoing >>"$LOG" 2>&1
}
# fw_allow <port>/<proto>, or a bare port meaning tcp
fw_prepare(){
  if [[ "$FW_TOOL" == firewalld ]]; then
    systemctl enable --now firewalld >>"$LOG" 2>&1 \
      || die "firewalld could not be started"
  fi
}
fw_allow(){
  local spec="$1" port="${1%%/*}" proto="${1#*/}"
  [[ "$proto" == "$port" ]] && proto=tcp
  if [[ "$FW_TOOL" == firewalld ]]; then
    firewall-cmd --permanent --add-port="${port//:/-}/${proto}" >>"$LOG" 2>&1 \
      || die "firewalld could not allow $spec"
  else
    ufw allow "$spec" >>"$LOG" 2>&1 || die "ufw could not allow $spec"
  fi
}
fw_allow_from(){   # <cidr> <port>
  local family=ipv4 rich_rule
  [[ "$1" == *:* ]] && family=ipv6
  if [[ "$FW_TOOL" == firewalld ]]; then
    rich_rule="rule family=\"${family}\" source address=\"$1\" port port=\"$2\" protocol=\"tcp\" accept"
    firewall-cmd --permanent --add-rich-rule="$rich_rule" \
      >>"$LOG" 2>&1 || die "firewalld could not restrict port $2 to $1"
  else
    ufw allow from "$1" to any port "$2" proto tcp >>"$LOG" 2>&1 \
      || die "ufw could not restrict port $2 to $1"
  fi
}
fw_commit(){
  if [[ "$FW_TOOL" == firewalld ]]; then
    firewall-cmd --reload >>"$LOG" 2>&1 || die "firewalld reload failed"
  else
    ufw --force enable >>"$LOG" 2>&1 || die "ufw could not be enabled"
  fi
}

# ---- Enterprise Linux repositories ---------------------------------------
# CRB must be enabled before EPEL. Remi's safe repository provides parallel,
# versioned PHP Software Collections without replacing the system PHP.
if [[ "$PKG_FAMILY" == rhel ]]; then
  say "Enabling CRB, EPEL and Remi"
  dnf -y -q config-manager --set-enabled crb >>"$LOG" 2>&1     || dnf -y -q config-manager --set-enabled powertools >>"$LOG" 2>&1     || die "Could not enable the CRB repository"
  pkg_install epel-release || die "Could not enable EPEL"
  if has_role web && [[ "${HP_MULTI_PHP_REPO:-auto}" != off ]]; then
    pkg_install "https://rpms.remirepo.net/enterprise/remi-release-${EL_MAJOR}.rpm"       || die "Could not enable the Remi PHP repository"
  fi
  pkg_refresh
  ok "CRB, EPEL and Remi repositories enabled"
fi

# Rspamd publishes supported repositories for Debian/Ubuntu and EL 8–10.  Use
# those rather than hoping a distribution snapshot happens to carry a current
# build; mail filtering is a required role component, not an optional extra.
if has_role mail && [[ "${HP_RSPAMD_REPO:-auto}" != off ]]; then
  say "Enabling the official Rspamd repository"
  if [[ "$PKG_FAMILY" == rhel ]]; then
    curl -fsSLo /etc/yum.repos.d/rspamd.repo       "https://rspamd.com/rpm-stable/centos-${EL_MAJOR}/rspamd.repo"       || die "Could not enable the Rspamd repository"
  else
    curl -fsSL https://rspamd.com/apt-stable/gpg.key       | gpg --dearmor --yes -o /usr/share/keyrings/rspamd.gpg       || die "Could not install the Rspamd repository key"
    codename="$DIST_CODENAME"
    printf 'deb [signed-by=/usr/share/keyrings/rspamd.gpg] https://rspamd.com/apt-stable/ %s main\n' "$codename"       >/etc/apt/sources.list.d/rspamd.list
  fi
  pkg_refresh
  ok "Rspamd repository enabled"
fi

if has_role web && [[ "${HP_MULTI_PHP_REPO:-auto}" != off ]]; then
  ensure_bootstrap_temp_dir
  if [[ "$PKG_FAMILY" == debian ]]; then
    if [[ "$ID" == ubuntu ]]; then
      add-apt-repository -y ppa:ondrej/php >>"$LOG" 2>&1 || die "Could not enable the PHP repository"
    else
      SURY_KEYRING_DEB="$BOOTSTRAP_TEMP_DIR/debsuryorg-archive-keyring.deb"
      curl -fsSLo "$SURY_KEYRING_DEB" https://packages.sury.org/debsuryorg-archive-keyring.deb >>"$LOG" 2>&1 || die "Could not download the PHP repository keyring"
      dpkg -i "$SURY_KEYRING_DEB" >>"$LOG" 2>&1 || die "Could not install the PHP repository keyring"
      rm -f "$SURY_KEYRING_DEB"
      echo "deb [signed-by=/usr/share/keyrings/debsuryorg-archive-keyring.gpg] https://packages.sury.org/php/ ${DIST_CODENAME} main"         >/etc/apt/sources.list.d/hostpanel-php.list
    fi
  fi
  LITESPEED_REPO_SCRIPT="$BOOTSTRAP_TEMP_DIR/litespeed-repo.sh"
  curl -fsSL https://repo.litespeed.sh -o "$LITESPEED_REPO_SCRIPT" >>"$LOG" 2>&1 || die "Could not download the LiteSpeed repository installer"
  bash "$LITESPEED_REPO_SCRIPT" >>"$LOG" 2>&1 || die "Could not enable the LiteSpeed repository"
  rm -f "$LITESPEED_REPO_SCRIPT"
fi
pkg_refresh

COMMON_PACKAGES=(openssl rsync acl gnupg sqlite3 needrestart inotify-tools smartmontools prometheus-node-exporter iproute2 git ca-certificates python3 python3-venv python3-pip curl ufw fail2ban unzip sudo nginx)
CONTROL_PACKAGES=(postgresql postgresql-contrib nodejs npm podman restic dnsutils)
pkg_available "$(pkg_name podman-compose)" && CONTROL_PACKAGES+=(podman-compose)
WEB_PACKAGES=(nginx apache2 apache2-utils libapache2-mod-fcgid composer certbot python3-certbot-nginx libfcgi-bin libnginx-mod-http-modsecurity modsecurity-crs radicale quota quotatool xfsprogs)
DATABASE_PACKAGES=(mariadb-server postgresql postgresql-contrib redis-server)
DNS_PACKAGES=(bind9 bind9utils)
MAIL_COMMON_PACKAGES=(dovecot-core dovecot-imapd dovecot-lmtpd dovecot-sieve dovecot-managesieved opendkim opendkim-tools rspamd redis-server clamav clamav-daemon)
POSTFIX_PACKAGES=(postfix postfix-pcre opendkim postsrsd)
EXIM_PACKAGES=(exim4-base exim4-config exim4-daemon-heavy)
BACKUP_PACKAGES=(rsync rclone gnupg restic)
EDGE_PACKAGES=(nginx certbot python3-certbot-nginx)
PACKAGES=("${COMMON_PACKAGES[@]}")
[[ "$PKG_FAMILY" == rhel ]] && PACKAGES+=(policycoreutils-python-utils selinux-policy-targeted)
has_role control && PACKAGES+=("${CONTROL_PACKAGES[@]}")
has_role web && PACKAGES+=("${WEB_PACKAGES[@]}")
has_role database && PACKAGES+=("${DATABASE_PACKAGES[@]}")
has_role dns && PACKAGES+=("${DNS_PACKAGES[@]}")
if has_role mail; then
  PACKAGES+=("${MAIL_COMMON_PACKAGES[@]}")
  if [[ "$MAIL_MTA" == exim ]]; then PACKAGES+=("${EXIM_PACKAGES[@]}"); else PACKAGES+=("${POSTFIX_PACKAGES[@]}"); fi
fi
has_role backup && PACKAGES+=("${BACKUP_PACKAGES[@]}")
has_role edge && PACKAGES+=("${EDGE_PACKAGES[@]}")
# Roundcube is installed from a pinned, checksummed complete upstream release
# below. Distribution packages differ too much between supported releases and
# can lag security updates, so webmail is deliberately not sourced from apt/EPEL.
if [[ "${HP_ENABLE_FTP:-no}" == yes ]] && has_role web; then PACKAGES+=(vsftpd db-util); fi
# Only observability/convenience packages may be absent. Everything that backs a
# selected hosting capability is required: silently dropping one creates a
# server that claims a role it cannot perform.
readarray -t OPTIONAL_PACKAGES < <(pkg_map needrestart smartmontools prometheus-node-exporter podman-compose)
OPTIONAL_SET=" $(printf '%s ' "${OPTIONAL_PACKAGES[@]}")"
MAPPED_PACKAGES=(); MISSING_REQUIRED=(); MISSING_OPTIONAL=()
while IFS= read -r package; do
  [[ -n "$package" ]] || continue
  if pkg_available "$package"; then
    MAPPED_PACKAGES+=("$package")
  elif [[ "$OPTIONAL_SET" == *" $package "* ]]; then
    MISSING_OPTIONAL+=("$package")
  else
    MISSING_REQUIRED+=("$package")
  fi
done < <(pkg_map "${PACKAGES[@]}")
((${#MISSING_REQUIRED[@]} == 0))   || die "Required packages unavailable on $PRETTY_NAME: ${MISSING_REQUIRED[*]}"
((${#MAPPED_PACKAGES[@]})) || die "No role packages are available from the configured repositories"
if [[ "$PKG_FAMILY" == debian && "$ID" == debian && "$VERSION_ID" == 13* ]]; then
  ok "Debian 13 package names resolved: PostgreSQL contrib is bundled; BIND utilities use current package names"
fi
pkg_install "${MAPPED_PACKAGES[@]}" || die "Could not install the selected role packages"
((${#MISSING_OPTIONAL[@]}))   && printf 'Optional packages unavailable on %s: %s\n' "$PRETTY_NAME" "${MISSING_OPTIONAL[*]}" >>"$LOG"
if has_role database; then
  PG_MAJOR="$(pg_config --version 2>/dev/null | awk '{print $2}' | cut -d. -f1 || true)"
  OPTIONAL_PG=()
  for candidate in "postgresql-$PG_MAJOR-postgis-3" "postgresql-$PG_MAJOR-pgvector"; do
    [[ -n "$PG_MAJOR" ]] && pkg_available "$candidate" && OPTIONAL_PG+=("$candidate")
  done
  ((${#OPTIONAL_PG[@]})) && pkg_install "${OPTIONAL_PG[@]}" || true
fi
ok "role packages installed"

if has_role web; then
say "Installing supported PHP-FPM branches"
PHP_BRANCHES=(8.5 8.4 8.3 8.2)
if [[ "${HP_PHP_LEGACY:-no}" == yes ]]; then PHP_BRANCHES+=(8.1 8.0 7.4); fi
PHP_SUFFIXES=(fpm cli common mysql curl mbstring xml zip gd intl bcmath soap opcache readline pgsql sqlite3 redis memcached apcu imagick bz2 gmp ldap imap igbinary msgpack ssh2 snmp yaml tidy pspell mailparse protobuf lz4 zstd)
PHP_OPTIONAL_SUFFIXES=(oauth amqp zmq mongodb grpc swoole ds uuid)
if [[ "${HP_PHP_FULL:-no}" == yes ]]; then PHP_SUFFIXES+=("${PHP_OPTIONAL_SUFFIXES[@]}"); fi
PHP_INSTALLED=(); mkdir -p /run/php
for version in "${PHP_BRANCHES[@]}"; do
  packages=(); seen=" "
  for suffix in "${PHP_SUFFIXES[@]}"; do
    package="$(php_package_for "$version" "$suffix" || true)"
    [[ -n "$package" && "$seen" != *" $package "* ]] || continue
    seen+="$package "; packages+=("$package")
  done
  required_fpm="$(php_package_for "$version" fpm || true)"
  required_cli="$(php_package_for "$version" cli || true)"
  [[ -n "$required_fpm" && -n "$required_cli" ]] || continue
  [[ "$seen" == *" $required_fpm "* ]] || packages+=("$required_fpm")
  [[ "$seen" == *" $required_cli "* ]] || packages+=("$required_cli")
  pkg_install "${packages[@]}" || die "Could not install PHP $version"
  service="$(php_service "$version")"; fpm="$(php_fpm_bin "$version")"; pool="$(php_pool_dir "$version")"
  mkdir -p "$pool"
  if [[ "$PKG_FAMILY" == rhel ]]; then
    [[ -f "$pool/www.conf" ]] && mv -f "$pool/www.conf" "$pool/www.conf.disabled"
    cat >"$pool/hostpanel-shared.conf" <<EOF
[hostpanel-shared]
user = $WEB_USER
group = $WEB_USER
listen = /run/php/php${version}-fpm.sock
listen.owner = $WEB_USER
listen.group = $WEB_USER
listen.mode = 0660
pm = ondemand
pm.max_children = 12
pm.process_idle_timeout = 10s
catch_workers_output = yes
clear_env = yes
security.limit_extensions = .php .phtml .inc
EOF
  fi
  systemctl enable --now "$service" >>"$LOG" 2>&1 || die "PHP $version FPM failed to start"
  "$fpm" -t >>"$LOG" 2>&1 || die "PHP $version FPM configuration is invalid"
  PHP_INSTALLED+=("$version")
done
((${#PHP_INSTALLED[@]})) || die "No versioned PHP-FPM branch could be installed"
mkdir -p /etc/hostpanel
printf '%s\n' "${PHP_INSTALLED[@]}" >/etc/hostpanel/php-versions
ok "PHP-FPM installed: ${PHP_INSTALLED[*]}"

fi

# Dovecot full-text search is optional on distributions where the Xapian
# plugin package is published. The panel reports it as unavailable rather than
# pretending indexing works when the package is missing.
if has_role mail; then
if pkg_available "$(pkg_name dovecot-fts-xapian)"; then
  pkg_install "$(pkg_name dovecot-fts-xapian)" || true
fi

fi

if has_role web; then
say "Installing OpenLiteSpeed and LSPHP"
OLS_VERSIONS=()
OLS_SKIPPED_MODULES=()
OLS_BRANCHES=(85 84 83 82)
if [[ "${HP_PHP_LEGACY:-no}" == yes ]]; then OLS_BRANCHES+=(81 80 74); fi
OLS_SUFFIXES=(common mysql curl mbstring xml zip gd opcache intl bcmath soap pgsql sqlite3 redis memcached apcu imagick bz2 gmp ldap imap igbinary msgpack ssh2 snmp yaml tidy pspell mailparse protobuf lz4 zstd)
OLS_OPTIONAL_SUFFIXES=(oauth amqp zmq mongodb grpc swoole ds uuid)
if [[ "${HP_PHP_FULL:-no}" == yes ]]; then OLS_SUFFIXES+=("${OLS_OPTIONAL_SUFFIXES[@]}"); fi

# Install the server independently. Optional extension availability must never
# make the OpenLiteSpeed package itself fail.
pkg_install "$(pkg_name openlitespeed)" \
  || die "Could not install OpenLiteSpeed"

for version in "${OLS_BRANCHES[@]}"; do
  runtime="lsphp$version"
  if ! pkg_available "$runtime"; then
    warn "LSPHP $version is not published for this OS/architecture; skipping that branch"
    continue
  fi
  if ! pkg_try_install "$runtime"; then
    warn "LSPHP $version runtime could not be installed; skipping that branch (see $LOG)"
    continue
  fi

  OLS_VERSIONS+=("$version")
  branch_packages=()
  for suffix in "${OLS_SUFFIXES[@]}"; do
    package="lsphp$version-$suffix"
    if pkg_available "$package"; then
      branch_packages+=("$package")
    else
      OLS_SKIPPED_MODULES+=("$package")
    fi
  done

  # Install the available extension set as one transaction for speed. If a
  # repository changes between metadata refresh and installation, retry each
  # package independently and continue past only the failing optional module.
  if ((${#branch_packages[@]})) && ! pkg_try_install "${branch_packages[@]}"; then
    warn "The LSPHP $version extension bundle changed or contains a conflicting package; retrying modules individually"
    for package in "${branch_packages[@]}"; do
      if ! pkg_try_install "$package"; then
        OLS_SKIPPED_MODULES+=("$package")
        warn "Optional LSPHP module skipped: $package (see $LOG)"
      fi
    done
  fi

  [[ -x "/usr/local/lsws/lsphp$version/bin/lsphp" ]] \
    || die "LSPHP $version package installed without /usr/local/lsws/lsphp$version/bin/lsphp"
done

if ((${#OLS_VERSIONS[@]})); then
  DEFAULT_LSPHP="${OLS_VERSIONS[0]}"
  ln -sf "/usr/local/lsws/lsphp$DEFAULT_LSPHP/bin/lsphp" /usr/local/lsws/fcgi-bin/lsphp
  mkdir -p /etc/hostpanel
  printf '%s\n' "${OLS_VERSIONS[@]}" >/etc/hostpanel/lsphp-versions
  ok "OpenLiteSpeed installed with LSPHP: ${OLS_VERSIONS[*]}"
  if ((${#OLS_SKIPPED_MODULES[@]})); then
    printf '%s\n' "${OLS_SKIPPED_MODULES[@]}" | sort -u >/etc/hostpanel/lsphp-skipped-packages
    warn "Some optional LSPHP packages were unavailable; recorded in /etc/hostpanel/lsphp-skipped-packages"
  else
    rm -f /etc/hostpanel/lsphp-skipped-packages
  fi
else
  [[ -x /usr/local/lsws/fcgi-bin/lsphp ]] \
    || die "OpenLiteSpeed installed without an LSPHP runtime"
  rm -f /etc/hostpanel/lsphp-versions /etc/hostpanel/lsphp-skipped-packages
  ok "OpenLiteSpeed installed with its bundled LSPHP runtime"
fi

fi

# --------------------------------------------------------------------------- #
say "Configuring the firewall"
# Never reset an existing firewall. A control-panel installer must not erase
# VPN, monitoring, cluster or provider-management rules that it did not create.
mkdir -p "$BACKUP_DIR/install"
fw_snapshot "$BACKUP_DIR/install/firewall-before.txt"
FW_WAS_ACTIVE=no
fw_is_active && FW_WAS_ACTIVE=yes
fw_prepare
if [[ "$FW_WAS_ACTIVE" == no ]]; then
  fw_default_deny
fi
FIREWALL_PORTS=(22/tcp)
if has_role web || has_role edge; then FIREWALL_PORTS+=(80/tcp 443/tcp); fi
if has_role mail; then FIREWALL_PORTS+=(25/tcp 587/tcp 993/tcp); fi
if has_role dns; then FIREWALL_PORTS+=(53/tcp 53/udp); fi
for port in "${FIREWALL_PORTS[@]}"; do
  fw_allow "$port"
done

# The panel is opened only to the address running the installer. Everything
# else has to be added deliberately, because a control panel exposed to the
# whole internet is the one thing here that must not be a default.
ADMIN_IP="${SSH_CLIENT:-}"
ADMIN_IP="${ADMIN_IP%% *}"
if [[ -n "$ADMIN_IP" ]]; then
  ADMIN_IP="$(python3 - "$ADMIN_IP" <<'PYIP'
import ipaddress
import sys

try:
    address = ipaddress.ip_address(sys.argv[1])
except ValueError as exc:
    raise SystemExit(f"Invalid SSH_CLIENT source address: {exc}")
if getattr(address, "scope_id", None):
    raise SystemExit("Invalid SSH_CLIENT source address: scoped IPv6 is unsupported")
print(address)
PYIP
)" || die "Invalid SSH_CLIENT source address"
  # Remove only the installer's old broad rule, if one exists. Existing custom
  # source-restricted rules are preserved.
  [[ "$FW_TOOL" == ufw ]] && ufw --force delete allow "${PANEL_PORT}/tcp" >>"$LOG" 2>&1 || true
  fw_allow_from "$ADMIN_IP" "$PANEL_PORT"
  PANEL_ACCESS="restricted to $ADMIN_IP"
else
  fw_allow "${PANEL_PORT}/tcp"
  PANEL_ACCESS="OPEN TO THE INTERNET — restrict it, see below"
fi
fw_commit
if [[ "$FW_TOOL" == firewalld ]]; then
  RESTRICT_HINT="          firewall-cmd --permanent --remove-port=$PANEL_PORT/tcp
          firewall-cmd --permanent --add-rich-rule='rule family=\"ipv4\" source address=\"YOUR.IP.HERE\" port port=$PANEL_PORT protocol=\"tcp\" accept'
          firewall-cmd --reload"
else
  RESTRICT_HINT="          ufw delete allow $PANEL_PORT/tcp
          ufw allow from YOUR.IP.HERE to any port $PANEL_PORT proto tcp"
fi

# --------------------------------------------------------------------------- #
say "Creating service accounts and directories"
id "$PANEL_USER" &>/dev/null || useradd --system --home-dir "$PANEL_DIR" --shell /usr/sbin/nologin "$PANEL_USER"
if has_role mail; then
  getent group vmail >/dev/null || groupadd -g "$VMAIL_UID" vmail
  id vmail &>/dev/null || useradd -g vmail -u "$VMAIL_UID" -d "$MAIL_ROOT" -s /usr/sbin/nologin vmail
fi
getent group hostpanel-sftp >/dev/null || groupadd --system hostpanel-sftp
mkdir -p /etc/ssh/sshd_config.d /srv/hostpanel-sftp
cat >/etc/ssh/sshd_config.d/90-hostpanel-sftp.conf <<'EOF'
Match Group hostpanel-sftp
    ChrootDirectory %h
    ForceCommand internal-sftp
    PasswordAuthentication yes
    PermitTunnel no
    AllowAgentForwarding no
    AllowTcpForwarding no
    X11Forwarding no
EOF
sshd -t >>"$LOG" 2>&1 || die "HostPanel SFTP configuration is invalid"
systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || true

mkdir -p "$PANEL_DIR" "$VHOST_ROOT" "$BACKUP_DIR"
if has_role mail; then
  mkdir -p "$MAIL_ROOT" /etc/opendkim/keys
  chown -R vmail:vmail "$MAIL_ROOT"
fi
if has_role dns; then
  mkdir -p "$ZONE_DIR" "$(dirname "$NAMED_MANAGED_CONF")"
  chown "$ZONE_OWNER" "$ZONE_DIR"
  chmod 750 "$ZONE_DIR"
  touch "$NAMED_MANAGED_CONF"
  chown root:"${ZONE_OWNER#*:}" "$NAMED_MANAGED_CONF"
  chmod 640 "$NAMED_MANAGED_CONF"
  if [[ "$PKG_FAMILY" == rhel ]]; then
    grep -qF 'include "/etc/named/hostpanel-zones.conf";' /etc/named.conf \
      || printf '\ninclude "/etc/named/hostpanel-zones.conf";\n' >>/etc/named.conf
    # The stock EL configuration is recursive and localhost-only. A hosting
    # node is authoritative on port 53; make only those narrow substitutions.
    sed -ri 's/listen-on port 53[[:space:]]*\{[^}]*\};/listen-on port 53 { any; };/;
             s/listen-on-v6 port 53[[:space:]]*\{[^}]*\};/listen-on-v6 port 53 { any; };/;
             s/allow-query[[:space:]]*\{[[:space:]]*localhost;[[:space:]]*\};/allow-query { any; };/;
             s/recursion[[:space:]]+yes;/recursion no;/' /etc/named.conf
  fi
fi
if [[ "${HP_ENABLE_FTP:-no}" == yes ]] && has_role web; then mkdir -p /etc/vsftpd/users; fi
ok "HostPanel service accounts and role directories ready"

if has_role dns; then
  say "Configuring authoritative DNS"
  named-checkconf >>"$LOG" 2>&1 || die "BIND configuration is invalid"
  systemctl enable --now "$(svc dns)" >>"$LOG" 2>&1 || die "DNS service failed to start"
  ok "$(svc dns) serves managed zones from $ZONE_DIR"
fi

# --------------------------------------------------------------------------- #
sync_release_tree(){
  local source="$1" destination="$2" stage backup=""
  [[ -d "$source" ]] || return 1
  stage="${destination}.installing.$$"
  rm -rf "$stage"
  mkdir -p "$stage"
  rsync -a --delete --exclude='__pycache__/' --exclude='*.pyc' \
    "$source/" "$stage/" >>"$LOG" 2>&1 \
    || die "Could not stage $source for $destination"
  if [[ -e "$destination" ]]; then
    backup="${destination}.previous.$$"
    rm -rf "$backup"
    mv "$destination" "$backup"
  fi
  mv "$stage" "$destination"
  TREE_ROLLBACK_DESTS+=("$destination")
  TREE_ROLLBACK_BACKUPS+=("$backup")
}

sync_optional_tree(){
  local source="$1" destination="$2" backup=""
  if [[ -d "$source" ]]; then
    sync_release_tree "$source" "$destination"
  elif [[ "$REINSTALL" == yes && -e "$destination" ]]; then
    backup="${destination}.previous.$$"
    rm -rf "$backup"
    mv "$destination" "$backup"
    TREE_ROLLBACK_DESTS+=("$destination")
    TREE_ROLLBACK_BACKUPS+=("$backup")
  fi
}

say "Installing HostPanel application files"
SOURCE_ROOT=""
if [[ -d "$SCRIPT_DIR/app" ]]; then
  SOURCE_ROOT="$SCRIPT_DIR"
else
  [[ -n "$REPO" ]] || die "Run from an extracted source release or set HP_REPO to a reviewed Git repository"
  [[ "$REPO" =~ ^https://[^[:space:]]+$ ]] || die "HP_REPO must be an HTTPS Git repository URL"
  [[ "$REPO_REF" =~ ^[0-9a-fA-F]{40}$ ]] \
    || die "HP_REPO_REF must be a full 40-character Git commit SHA"
  SOURCE_TEMP_DIR="$(mktemp -d /tmp/hostpanel-src.XXXXXX)" \
    || die "Could not create a private temporary source directory"
  git -C "$SOURCE_TEMP_DIR" init -q \
    || die "Could not initialise the temporary Git checkout"
  git -C "$SOURCE_TEMP_DIR" remote add origin "$REPO" \
    || die "Could not configure the reviewed Git repository"
  git -C "$SOURCE_TEMP_DIR" fetch --depth 1 origin "$REPO_REF" \
    >>"$LOG" 2>&1 || die "Could not fetch reviewed commit $REPO_REF from $REPO"
  FETCHED_REPO_COMMIT="$(git -C "$SOURCE_TEMP_DIR" rev-parse --verify "FETCH_HEAD^{commit}")" \
    || die "Could not resolve fetched Git commit"
  [[ "${FETCHED_REPO_COMMIT,,}" == "${REPO_REF,,}" ]] \
    || die "Fetched Git commit does not match HP_REPO_REF"
  git -C "$SOURCE_TEMP_DIR" checkout -q --detach "$FETCHED_REPO_COMMIT" \
    >>"$LOG" 2>&1 || die "Could not check out reviewed commit $REPO_REF"
  SOURCE_ROOT="$SOURCE_TEMP_DIR"
fi
sync_release_tree "$SOURCE_ROOT/app" "$PANEL_DIR/app"
sync_optional_tree "$SOURCE_ROOT/releases" "$PANEL_DIR/releases"
sync_optional_tree "$SOURCE_ROOT/sdk" "$PANEL_DIR/sdk"
sync_optional_tree "$SOURCE_ROOT/tools" "$PANEL_DIR/tools"
sync_optional_tree "$SOURCE_ROOT/ops" "$PANEL_DIR/ops"
sync_optional_tree "$SOURCE_ROOT/integrations" "$PANEL_DIR/integrations"
[[ -f "$SOURCE_ROOT/VERSION" && -r "$SOURCE_ROOT/VERSION" && -s "$SOURCE_ROOT/VERSION" ]] \
  || die "VERSION must be a readable non-empty regular file in the release"
[[ -f "$SOURCE_ROOT/requirements.lock" ]] || die "requirements.lock is missing from the release"
RELEASE_VERSION="$(cat "$SOURCE_ROOT/VERSION")" \
  || die "Could not read VERSION from the release"
[[ "$RELEASE_VERSION" =~ ^[0-9]+(\.[0-9]+){2}([-+][0-9A-Za-z][0-9A-Za-z.-]*)?$ ]] \
  || die "VERSION must contain a safe SemVer-like value"
printf '%s\n' "$RELEASE_VERSION" >"$PANEL_DIR/VERSION"
cp -f "$SOURCE_ROOT/requirements.lock" "$PANEL_DIR/requirements.lock"
ok "panel files synchronised without nested or stale application directories"

# Cluster transfers use non-interactive, key-only receiver accounts whose
# shells accept only a bounded rsync receiver or Dovecot dsync command.
chmod 0755 "$PANEL_DIR/app/hostpanel-cluster-setup" \
  "$PANEL_DIR/app/hostpanel-cluster-receiver" "$PANEL_DIR/app/hostpanel-mail-dsync"
"$PANEL_DIR/app/hostpanel-cluster-setup" >>"$LOG" 2>&1 \
  || die "Could not configure the restricted cluster receiver accounts"
ok "restricted cluster receiver accounts ready"

# --------------------------------------------------------------------------- #
say "Installing the panel runtime"
RUNTIME_VERSION="$RELEASE_VERSION"
if [[ -L "$PANEL_DIR/venv" ]]; then
  PREVIOUS_VENV_TARGET="$(readlink "$PANEL_DIR/venv")"
fi
RUNTIME_DIR="$PANEL_DIR/venvs/$RUNTIME_VERSION"
RUNTIME_BUILD_DIR="$PANEL_DIR/venvs/.${RUNTIME_VERSION}.installing.$$"
RUNTIME_OLD_DIR=""
mkdir -p "$PANEL_DIR/venvs"
rm -rf "$RUNTIME_BUILD_DIR"
python3 -m venv "$RUNTIME_BUILD_DIR"
"$RUNTIME_BUILD_DIR/bin/pip" install --quiet --upgrade "pip==25.1.1" >>"$LOG" 2>&1
# Direct runtime dependencies are version locked. The release SBOM records the
# transitive packages installed for the target Python and operating system.
[[ -f "$PANEL_DIR/requirements.lock" ]] || die "requirements.lock is missing from the release"
"$RUNTIME_BUILD_DIR/bin/pip" install --quiet --require-hashes -r "$PANEL_DIR/requirements.lock" >>"$LOG" 2>&1
if [[ -d "$RUNTIME_DIR" ]]; then
  RUNTIME_OLD_DIR="$PANEL_DIR/venvs/.${RUNTIME_VERSION}.previous.$$"
  rm -rf "$RUNTIME_OLD_DIR"
  mv "$RUNTIME_DIR" "$RUNTIME_OLD_DIR"
fi
mv "$RUNTIME_BUILD_DIR" "$RUNTIME_DIR"
RUNTIME_REPLACED=yes
ln -sfn "venvs/$RUNTIME_VERSION" "$PANEL_DIR/venv"
ok "fresh version-locked runtime activated in venvs/$RUNTIME_VERSION"

if has_role web; then
say "Installing verified WP-CLI"
DEFAULT_PHP_VERSION="${PHP_INSTALLED[0]}"
DEFAULT_PHP_CLI="$(php_cli_bin "$DEFAULT_PHP_VERSION")"
[[ -x "$DEFAULT_PHP_CLI" ]] || die "Default PHP CLI is missing"
WPCLI_VERSION="2.12.0"
WPCLI_SHA256="ce34ddd838f7351d6759068d09793f26755463b4a4610a5a5c0a97b68220d85c"
if [[ ! -x /usr/local/bin/wp ]] || ! /usr/local/bin/wp cli version 2>/dev/null | grep -q "$WPCLI_VERSION"; then
  WPCLI_TEMP="$(mktemp /tmp/wp-cli.XXXXXX.phar)"
  curl -fsSL "https://github.com/wp-cli/wp-cli/releases/download/v${WPCLI_VERSION}/wp-cli-${WPCLI_VERSION}.phar" \
    -o "$WPCLI_TEMP" >>"$LOG" 2>&1 || die "Could not download WP-CLI $WPCLI_VERSION"
  echo "$WPCLI_SHA256  $WPCLI_TEMP" | sha256sum -c - >>"$LOG" 2>&1 || \
    die "WP-CLI checksum verification failed"
  "$DEFAULT_PHP_CLI" "$WPCLI_TEMP" --info >>"$LOG" 2>&1 || die "Downloaded WP-CLI cannot start"
  install -m 755 "$WPCLI_TEMP" /usr/local/bin/wp
  rm -f "$WPCLI_TEMP"
fi
if ! command -v php >/dev/null 2>&1; then
  ln -sfn "$DEFAULT_PHP_CLI" /usr/local/bin/php
fi
/usr/local/bin/wp --info --allow-root >>"$LOG" 2>&1 || die "WP-CLI verification failed"
ok "WP-CLI installed"
fi

# --------------------------------------------------------------------------- #
say "Creating or preserving panel state"
ADMIN_PASS="$(random_alnum 20)"
ADMIN_CREATED=no

mkdir -p "$PANEL_DIR/credentials"
chmod 700 "$PANEL_DIR/credentials"
if [[ -n "${HP_MASTER_KEY_SOURCE:-}" ]]; then
  [[ -f "$HP_MASTER_KEY_SOURCE" ]] || die "HP_MASTER_KEY_SOURCE does not exist"
  install -o root -g root -m 600 "$HP_MASTER_KEY_SOURCE" "$PANEL_DIR/credentials/master.key"
elif [[ ! -f "$PANEL_DIR/credentials/master.key" ]]; then
  random_alnum 64 >"$PANEL_DIR/credentials/master.key"
fi
chmod 600 "$PANEL_DIR/credentials/master.key"
mkdir -p /etc/hostpanel /etc/hostpanel/node-secrets
[[ -f /etc/hostpanel/node.secret ]] || random_alnum 64 >/etc/hostpanel/node.secret
[[ -f /etc/hostpanel/node-secrets/1.secret ]] || install -m 600 /etc/hostpanel/node.secret /etc/hostpanel/node-secrets/1.secret
chmod 700 /etc/hostpanel/node-secrets
chmod 600 /etc/hostpanel/node.secret /etc/hostpanel/node-secrets/1.secret
printf 'roles=%s\n' "$ROLE_CSV" >/etc/hostpanel/roles.conf
printf '%s\n' "$MAIL_MTA" >/etc/hostpanel/mail-mta
chmod 600 /etc/hostpanel/roles.conf /etc/hostpanel/mail-mta
if [[ -f "$SOURCE_ROOT/releases/update.pub" ]]; then
  install -o root -g root -m 644 "$SOURCE_ROOT/releases/update.pub" /etc/hostpanel/update.pub
elif [[ -f "$PANEL_DIR/releases/update.pub" ]]; then
  install -o root -g root -m 644 "$PANEL_DIR/releases/update.pub" /etc/hostpanel/update.pub
fi

PANEL_HOST="$PREFLIGHT_HOST"
EXISTING_EXTERNAL_URL="$(config_value HP_EXTERNAL_URL || true)"
EXISTING_READINESS_TOKEN="$(config_value HP_READINESS_TOKEN || true)"
EXISTING_TRUSTED_PROXY_CIDRS="$(config_value HP_TRUSTED_PROXY_CIDRS || true)"
EXISTING_CONTROL_MEMBER="$(config_value HP_CONTROL_MEMBER || true)"
EXISTING_CONTROL_PRIORITY="$(config_value HP_CONTROL_PRIORITY || true)"
EXISTING_CONTROL_ELIGIBLE="$(config_value HP_CONTROL_ELIGIBLE || true)"
EXISTING_UPDATE_CHANNEL="$(config_value HP_UPDATE_CHANNEL || true)"
EXTERNAL_URL="${HP_EXTERNAL_URL:-${EXISTING_EXTERNAL_URL:-https://$PANEL_HOST:$PANEL_PORT}}"
[[ "$EXTERNAL_URL" =~ ^https://[A-Za-z0-9.:-]+/?$ ]] \
  || die "HP_EXTERNAL_URL must be a simple HTTPS origin without path or query"
READINESS_TOKEN="${HP_READINESS_TOKEN:-${EXISTING_READINESS_TOKEN:-$(random_alnum 48)}}"
EXISTING_PANEL_SECRET=""
if [[ -f "$PANEL_DIR/config.env" ]]; then
  EXISTING_PANEL_SECRET="$(sed -n 's/^HP_SECRET=//p' "$PANEL_DIR/config.env" | head -1)"
fi
if [[ -n "${HP_PANEL_SECRET_SOURCE:-}" ]]; then
  [[ -f "$HP_PANEL_SECRET_SOURCE" ]] || die "HP_PANEL_SECRET_SOURCE does not exist"
  PANEL_SECRET="$(head -1 "$HP_PANEL_SECRET_SOURCE")"
elif [[ -n "$EXISTING_PANEL_SECRET" ]]; then
  PANEL_SECRET="$EXISTING_PANEL_SECRET"
else
  PANEL_SECRET="$(random_alnum 48)"
fi
[[ "$PANEL_SECRET" =~ ^[A-Za-z0-9]{32,256}$ ]] || die "Panel secret must contain 32-256 alphanumeric characters"
CONTROL_MEMBER="${HP_CONTROL_MEMBER:-${EXISTING_CONTROL_MEMBER:-$PANEL_HOST}}"
[[ "$CONTROL_MEMBER" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]{1,63}$ ]] || die "HP_CONTROL_MEMBER is invalid"
CONTROL_PRIORITY="${HP_CONTROL_PRIORITY:-${EXISTING_CONTROL_PRIORITY:-100}}"
[[ "$CONTROL_PRIORITY" =~ ^[0-9]+$ ]] && ((CONTROL_PRIORITY >= 1 && CONTROL_PRIORITY <= 1000)) \
  || die "HP_CONTROL_PRIORITY must be between 1 and 1000"
CONTROL_ELIGIBLE="${HP_CONTROL_ELIGIBLE:-${EXISTING_CONTROL_ELIGIBLE:-true}}"
[[ "${CONTROL_ELIGIBLE,,}" =~ ^(1|0|true|false|yes|no|on|off)$ ]] \
  || die "HP_CONTROL_ELIGIBLE must be true or false"
TRUSTED_PROXY_INPUT="${HP_TRUSTED_PROXY_CIDRS:-$EXISTING_TRUSTED_PROXY_CIDRS}"
TRUSTED_PROXY_CIDRS="$($PANEL_DIR/venv/bin/python - "$TRUSTED_PROXY_INPUT" <<'PYPROXY'
import ipaddress
import sys
values = []
for raw in sys.argv[1].split(','):
    raw = raw.strip()
    if not raw:
        continue
    try:
        value = str(ipaddress.ip_network(raw, strict=False))
    except ValueError as exc:
        raise SystemExit(f"invalid HP_TRUSTED_PROXY_CIDRS entry {raw!r}: {exc}")
    if value not in values:
        values.append(value)
print(','.join(values))
PYPROXY
)" || die "HP_TRUSTED_PROXY_CIDRS contains an invalid IPv4/IPv6 network"

OLD_CONFIG_COPY=""
if [[ -f "$PANEL_DIR/config.env" ]]; then
  OLD_CONFIG_COPY="$(mktemp /tmp/hostpanel-config.XXXXXX)"
  cp -f "$PANEL_DIR/config.env" "$OLD_CONFIG_COPY"
fi
CONFIG_NEW="$PANEL_DIR/config.env.new"
cat >"$CONFIG_NEW" <<EOF
HP_SECRET=$PANEL_SECRET
HP_PORT=$PANEL_PORT
HP_PANEL_BACKEND_PORT=$PANEL_BACKEND_PORT
HP_VHOST_ROOT=$VHOST_ROOT
HP_BACKUP_DIR=$BACKUP_DIR
HP_DB=$PANEL_DIR/hostpanel.db
HP_DATABASE_URL_FILE=$PANEL_DIR/credentials/database-url
HP_MASTER_KEY_FILE=$PANEL_DIR/credentials/master.key
HP_PLUGIN_DIR=$PANEL_DIR/plugins
HP_UPDATE_CHANNEL=${HP_UPDATE_CHANNEL:-${EXISTING_UPDATE_CHANNEL:-stable}}
HP_UPDATE_PUBLIC_KEY=/etc/hostpanel/update.pub
HP_UPDATE_MANIFEST=$UPDATE_MANIFEST
HP_EXTERNAL_URL=$EXTERNAL_URL
HP_TRUSTED_HOSTS=$PANEL_HOST
HP_TRUSTED_PROXY_CIDRS=$TRUSTED_PROXY_CIDRS
HP_READINESS_TOKEN=$READINESS_TOKEN
HP_NODE_ROLES=$ROLE_CSV
HP_MAIL_MTA=$MAIL_MTA
HP_CONTROL_MEMBER=$CONTROL_MEMBER
HP_CONTROL_PRIORITY=$CONTROL_PRIORITY
HP_CONTROL_ELIGIBLE=$CONTROL_ELIGIBLE
EOF
if [[ -n "$OLD_CONFIG_COPY" ]]; then
  # Preserve custom HP_* keys that are not managed by this installer revision.
  grep -E '^[A-Z][A-Z0-9_]*=' "$OLD_CONFIG_COPY"     | grep -Ev '^(HP_SECRET|HP_PORT|HP_PANEL_BACKEND_PORT|HP_VHOST_ROOT|HP_BACKUP_DIR|HP_DB|HP_DATABASE_URL_FILE|HP_MASTER_KEY_FILE|HP_PLUGIN_DIR|HP_UPDATE_CHANNEL|HP_UPDATE_PUBLIC_KEY|HP_UPDATE_MANIFEST|HP_EXTERNAL_URL|HP_TRUSTED_HOSTS|HP_TRUSTED_PROXY_CIDRS|HP_READINESS_TOKEN|HP_NODE_ROLES|HP_MAIL_MTA|HP_CONTROL_MEMBER|HP_CONTROL_PRIORITY|HP_CONTROL_ELIGIBLE)='     >>"$CONFIG_NEW" || true
  rm -f "$OLD_CONFIG_COPY"
fi
chmod 600 "$CONFIG_NEW"
mv -f "$CONFIG_NEW" "$PANEL_DIR/config.env"

# SQLite is retained as an offline recovery snapshot. PostgreSQL is the normal
# control plane so multiple panel workers and remote controllers can coordinate
# through transactional row locks. Set HP_CONTROL_DB=sqlite before installation
# only for a deliberately single-server recovery-oriented deployment.
ADMIN_RESULT="$(HP_DATABASE_URL_FILE=/nonexistent HP_DB="$PANEL_DIR/hostpanel.db" "$PANEL_DIR/venv/bin/python" -c "
import sys; sys.path.insert(0, '$PANEL_DIR/app')
import store
created = store.init('admin', '$ADMIN_PASS')
print(created or '')
" 2>>"$LOG")" || die "Could not initialise the HostPanel recovery database"
if [[ -n "$ADMIN_RESULT" ]]; then
  ADMIN_CREATED=yes
  ADMIN_PASS="$ADMIN_RESULT"
else
  ADMIN_PASS=""
  ok "existing administrator accounts preserved"
fi
chmod 600 "$PANEL_DIR/hostpanel.db"

CONTROL_DB="${HP_CONTROL_DB:-postgresql}"
SHARED_DATABASE_URL=""
EXISTING_DATABASE_URL=""
if [[ -s "$PANEL_DIR/credentials/database-url" ]]; then
  EXISTING_DATABASE_URL="$(head -1 "$PANEL_DIR/credentials/database-url")"
fi
if [[ -n "${HP_SHARED_DATABASE_URL_FILE:-}" ]]; then
  [[ -f "$HP_SHARED_DATABASE_URL_FILE" ]] || die "HP_SHARED_DATABASE_URL_FILE does not exist"
  SHARED_DATABASE_URL="$(head -1 "$HP_SHARED_DATABASE_URL_FILE")"
  [[ "$SHARED_DATABASE_URL" == postgresql://* || "$SHARED_DATABASE_URL" == postgres://* ]] \
    || die "HP_SHARED_DATABASE_URL_FILE must contain a PostgreSQL URL"
fi
if ! has_role control; then
  CONTROL_DB=sqlite
  SHARED_DATABASE_URL=""
fi
if has_role control \
   && [[ "$CONTROL_DB" == postgresql \
      && -z "$SHARED_DATABASE_URL" \
      && "$REINSTALL" == yes \
      && "${HP_ROTATE_CONTROL_DB_PASSWORD:-no}" != yes \
      && -n "$EXISTING_DATABASE_URL" ]]; then
  SHARED_DATABASE_URL="$EXISTING_DATABASE_URL"
  ok "existing PostgreSQL control-plane credential preserved"
fi
if [[ -n "$SHARED_DATABASE_URL" ]]; then
  printf '%s\n' "$SHARED_DATABASE_URL" >"$PANEL_DIR/credentials/database-url"
  chmod 600 "$PANEL_DIR/credentials/database-url"
  HP_DB="$PANEL_DIR/hostpanel.db" HP_DATABASE_URL="$SHARED_DATABASE_URL" \
    "$PANEL_DIR/venv/bin/python" "$PANEL_DIR/app/hostpanel-controlplane-migrate" >>"$LOG" 2>&1 \
    || die "Could not initialise the shared PostgreSQL control plane"
  ok "shared PostgreSQL control plane initialised"
elif [[ "$CONTROL_DB" == postgresql ]]; then
  CONTROL_PASS="$(random_alnum 32)"
  sudo -u postgres psql -v ON_ERROR_STOP=1 <<SQL >>"$LOG" 2>&1
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='hostpanel_control') THEN
    CREATE ROLE hostpanel_control LOGIN PASSWORD '$CONTROL_PASS';
  ELSE
    ALTER ROLE hostpanel_control PASSWORD '$CONTROL_PASS';
  END IF;
END \$\$;
SELECT 'CREATE DATABASE hostpanel_control OWNER hostpanel_control'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname='hostpanel_control')\gexec
SQL
  printf 'postgresql://hostpanel_control:%s@127.0.0.1/hostpanel_control\n' "$CONTROL_PASS" >"$PANEL_DIR/credentials/database-url"
  chmod 600 "$PANEL_DIR/credentials/database-url"
  HP_DB="$PANEL_DIR/hostpanel.db" HP_DATABASE_URL="$(cat "$PANEL_DIR/credentials/database-url")" \
    "$PANEL_DIR/venv/bin/python" "$PANEL_DIR/app/hostpanel-controlplane-migrate" >>"$LOG" 2>&1 \
    || die "Could not initialise PostgreSQL control plane"
  ok "PostgreSQL control plane initialised; SQLite recovery snapshot retained"
else
  : >"$PANEL_DIR/credentials/database-url"
  chmod 600 "$PANEL_DIR/credentials/database-url"
  ok "SQLite recovery mode selected explicitly"
fi

# --------------------------------------------------------------------------- #
if has_role mail; then
say "Configuring mail ($MAIL_MTA)"
MAIL_TLS_KEY="/etc/ssl/private/hostpanel-mail.key"
MAIL_TLS_CERT="/etc/ssl/certs/hostpanel-mail.crt"
if [[ ! -s "$MAIL_TLS_KEY" || ! -s "$MAIL_TLS_CERT" ]]; then
  openssl req -x509 -newkey rsa:3072 -nodes -days 825 \
    -keyout "$MAIL_TLS_KEY" -out "$MAIL_TLS_CERT" \
    -subj "/CN=$PANEL_HOST" -addext "subjectAltName=DNS:$PANEL_HOST" \
    >>"$LOG" 2>&1 || die "Could not create the initial mail certificate"
fi
chown root:root "$MAIL_TLS_KEY" "$MAIL_TLS_CERT"
chmod 600 "$MAIL_TLS_KEY"; chmod 644 "$MAIL_TLS_CERT"

# The flat maps remain in their historical location so existing installations
# can switch MTA without rewriting panel state. Exim reads them with lsearch;
# Postfix compiles the address maps with postmap.
mkdir -p /etc/postfix /etc/hostpanel
for map in vmailbox vdomains valias sender_login; do touch "/etc/postfix/$map"; done
touch /etc/dovecot/users
MAIL_MESSAGES_PER_HOUR=200
MAIL_MAX_MESSAGE_BYTES=26214400
if [[ -r /etc/hostpanel/mail-limits.conf ]]; then
  current_rate="$(awk -F= '$1=="messages_per_hour"{print $2}' /etc/hostpanel/mail-limits.conf | tail -1)"
  current_size="$(awk -F= '$1=="max_message_bytes"{print $2}' /etc/hostpanel/mail-limits.conf | tail -1)"
  [[ "$current_rate" =~ ^[0-9]+$ ]] && ((current_rate >= 1 && current_rate <= 100000)) && MAIL_MESSAGES_PER_HOUR="$current_rate"
  [[ "$current_size" =~ ^[0-9]+$ ]] && ((current_size >= 1048576 && current_size <= 209715200)) && MAIL_MAX_MESSAGE_BYTES="$current_size"
fi
printf 'messages_per_hour=%s\nmax_message_bytes=%s\n' \
  "$MAIL_MESSAGES_PER_HOUR" "$MAIL_MAX_MESSAGE_BYTES" >/etc/hostpanel/mail-limits.conf
chown root:"$PANEL_USER" /etc/hostpanel/mail-limits.conf
chmod 640 /etc/hostpanel/mail-limits.conf

if [[ "$MAIL_MTA" == postfix ]]; then
  chmod 640 /etc/postfix/vmailbox /etc/postfix/vdomains /etc/postfix/valias /etc/postfix/sender_login
  postmap /etc/postfix/vmailbox >>"$LOG" 2>&1 || true
  postmap /etc/postfix/valias >>"$LOG" 2>&1 || true
  postmap /etc/postfix/sender_login >>"$LOG" 2>&1 || true

  postconf -e "inet_protocols = all" >>"$LOG" 2>&1
  postconf -e "virtual_mailbox_domains = /etc/postfix/vdomains" >>"$LOG" 2>&1
  postconf -e "virtual_mailbox_base = $MAIL_ROOT" >>"$LOG" 2>&1
  postconf -e "virtual_mailbox_maps = hash:/etc/postfix/vmailbox" >>"$LOG" 2>&1
  postconf -e "virtual_alias_maps = hash:/etc/postfix/valias" >>"$LOG" 2>&1
  postconf -e "virtual_uid_maps = static:$VMAIL_UID" >>"$LOG" 2>&1
  postconf -e "virtual_gid_maps = static:$VMAIL_UID" >>"$LOG" 2>&1
  postconf -e "smtpd_tls_security_level = may" >>"$LOG" 2>&1
  postconf -e "smtpd_tls_cert_file = $MAIL_TLS_CERT" >>"$LOG" 2>&1
  postconf -e "smtpd_tls_key_file = $MAIL_TLS_KEY" >>"$LOG" 2>&1
  postconf -e "smtpd_tls_protocols = >=TLSv1.2" >>"$LOG" 2>&1
  postconf -e "smtp_tls_protocols = >=TLSv1.2" >>"$LOG" 2>&1
  postconf -e "smtpd_sasl_type = dovecot" >>"$LOG" 2>&1
  postconf -e "smtpd_sasl_path = private/auth" >>"$LOG" 2>&1
  postconf -e "smtpd_sasl_auth_enable = yes" >>"$LOG" 2>&1
  postconf -e "smtpd_sender_login_maps = hash:/etc/postfix/sender_login" >>"$LOG" 2>&1
  postconf -e "smtpd_relay_restrictions = permit_mynetworks,permit_sasl_authenticated,defer_unauth_destination" >>"$LOG" 2>&1
  postconf -e "anvil_rate_time_unit = 3600s" >>"$LOG" 2>&1
  postconf -e "smtpd_client_message_rate_limit = $MAIL_MESSAGES_PER_HOUR" >>"$LOG" 2>&1
  postconf -e "message_size_limit = $MAIL_MAX_MESSAGE_BYTES" >>"$LOG" 2>&1

  postconf -M 'submission/inet=submission inet n - y - - smtpd' >>"$LOG" 2>&1
  postconf -P 'submission/inet/syslog_name=postfix/submission' >>"$LOG" 2>&1
  postconf -P 'submission/inet/smtpd_tls_security_level=encrypt' >>"$LOG" 2>&1
  postconf -P 'submission/inet/smtpd_sasl_auth_enable=yes' >>"$LOG" 2>&1
  postconf -P 'submission/inet/smtpd_client_restrictions=permit_sasl_authenticated,reject' >>"$LOG" 2>&1
  postconf -P 'submission/inet/smtpd_sender_restrictions=reject_sender_login_mismatch' >>"$LOG" 2>&1
  postconf -P 'submission/inet/smtpd_recipient_restrictions=permit_sasl_authenticated,reject' >>"$LOG" 2>&1
  postconf -P 'submission/inet/milter_macro_daemon_name=ORIGINATING' >>"$LOG" 2>&1

  cat >/etc/dovecot/conf.d/99-hostpanel.conf <<EOF
# Managed by HostPanel — Postfix profile
mail_location = maildir:$MAIL_ROOT/%d/%n/Maildir
passdb { driver = passwd-file; args = /etc/dovecot/users }
userdb { driver = passwd-file; args = /etc/dovecot/users }
protocols = imap lmtp
ssl = required
ssl_min_protocol = TLSv1.2
ssl_cert = <$MAIL_TLS_CERT
ssl_key = <$MAIL_TLS_KEY
disable_plaintext_auth = yes
auth_mechanisms = plain login
service auth {
  unix_listener /var/spool/postfix/private/auth {
    mode = 0660
    user = postfix
    group = postfix
  }
}
EOF
  postfix check >>"$LOG" 2>&1 || die "Postfix configuration is invalid"
  MTA_UNIT=postfix
else
  if [[ "$PKG_FAMILY" == rhel ]]; then
    EXIM_GROUP=exim
    EXIM_CONF_DIR=/etc/exim
    EXIM_TEMPLATE=/etc/exim/exim.conf
    EXIM_BIN=/usr/sbin/exim
  else
    EXIM_GROUP=Debian-exim
    EXIM_CONF_DIR=/etc/exim4
    EXIM_TEMPLATE=/etc/exim4/exim4.conf.template
    EXIM_BIN=/usr/sbin/exim4
  fi
  chown root:"$EXIM_GROUP" /etc/postfix/vmailbox /etc/postfix/vdomains /etc/postfix/valias /etc/postfix/sender_login
  chmod 640 /etc/postfix/vmailbox /etc/postfix/vdomains /etc/postfix/valias /etc/postfix/sender_login
  install -d -o root -g "$EXIM_GROUP" -m 750 "$EXIM_CONF_DIR"
  touch "$EXIM_CONF_DIR/hostpanel-source-ip"
  cat >"$EXIM_CONF_DIR/hostpanel-system-filter" <<'EOF'
# Exim filter
# Managed by HostPanel. Mail-policy journaling rules are added by the worker.
EOF
  chown root:"$EXIM_GROUP" "$EXIM_CONF_DIR/hostpanel-source-ip" "$EXIM_CONF_DIR/hostpanel-system-filter"
  chmod 640 "$EXIM_CONF_DIR/hostpanel-source-ip" "$EXIM_CONF_DIR/hostpanel-system-filter"
  cat >"$EXIM_CONF_DIR/hostpanel-limits.conf" <<EOF
HOSTPANEL_MESSAGES_PER_HOUR = $MAIL_MESSAGES_PER_HOUR
HOSTPANEL_MAX_MESSAGE_BYTES = $MAIL_MAX_MESSAGE_BYTES
EOF
  chown root:"$EXIM_GROUP" "$EXIM_CONF_DIR/hostpanel-limits.conf"
  chmod 640 "$EXIM_CONF_DIR/hostpanel-limits.conf"
  if [[ "$PKG_FAMILY" == debian ]]; then
  cat >"$EXIM_CONF_DIR/update-exim4.conf.conf" <<EOF
dc_eximconfig_configtype='internet'
dc_other_hostnames=''
dc_local_interfaces=''
dc_readhost=''
dc_relay_domains=''
dc_minimaldns='false'
dc_relay_nets=''
dc_smarthost=''
CFILEMODE='644'
dc_use_split_config='false'
dc_hide_mailname=''
dc_mailname_in_oh='true'
dc_localdelivery='mail_spool'
EOF
  cat >/etc/mailname <<EOF
$PANEL_HOST
EOF
  fi
  cat >"$EXIM_TEMPLATE" <<EOF
# Managed by HostPanel — Exim profile. Regenerated by the installer.
.include_if_exists $EXIM_CONF_DIR/hostpanel-limits.conf

primary_hostname = $PANEL_HOST
qualify_domain = $PANEL_HOST
qualify_recipient = $PANEL_HOST
never_users = root
host_lookup = *
rfc1413_query_timeout = 0s
ignore_bounce_errors_after = 2d
timeout_frozen_after = 7d
message_size_limit = HOSTPANEL_MAX_MESSAGE_BYTES
daemon_smtp_ports = 25 : 587
smtp_active_hostname = \$primary_hostname

hostlist relay_from_hosts = 127.0.0.1 : ::1
domainlist local_domains = lsearch;/etc/postfix/vdomains

tls_advertise_hosts = *
tls_certificate = $MAIL_TLS_CERT
tls_privatekey = $MAIL_TLS_KEY
tls_require_ciphers = NORMAL:-VERS-ALL:+VERS-TLS1.2:+VERS-TLS1.3
auth_advertise_hosts = \${if eq{\$tls_in_cipher}{}{}{*}}

acl_smtp_mail = acl_check_mail
acl_smtp_rcpt = acl_check_rcpt
acl_smtp_data = acl_check_data
spamd_address = 127.0.0.1 11333 variant=rspamd
system_filter = $EXIM_CONF_DIR/hostpanel-system-filter
system_filter_user = $EXIM_GROUP
system_filter_group = $EXIM_GROUP

begin acl

acl_check_mail:
  deny  condition = \${if and{{eq{\$interface_port}{587}}{eq{\$tls_in_cipher}{}}}{yes}{no}}
        message = TLS is required on the submission port
  deny  condition = \${if and{{eq{\$interface_port}{587}}{!def:authenticated_id}}{yes}{no}}
        message = Authentication is required on the submission port
  deny  authenticated = *
        condition = \${if !eqi{\$sender_address}{\$authenticated_id}{yes}{no}}
        message = Authenticated users may send only as their own mailbox
  deny  authenticated = *
        ratelimit = HOSTPANEL_MESSAGES_PER_HOUR / 1h / per_mail / strict / \$authenticated_id
        message = Hourly outbound message limit exceeded
  accept

acl_check_rcpt:
  accept hosts = +relay_from_hosts
  deny  local_parts = ^[.] : ^.*[@%!/|]
  accept authenticated = *
  accept domains = +local_domains
         verify = recipient
  deny  message = Relay not permitted

acl_check_data:
  warn  spam = nobody:true
        add_header = X-Spam-Score: \$spam_score
        add_header = X-Spam-Report: \$spam_report
  warn  condition = \${if and{{def:spam_score_int}{>{\$spam_score_int}{60}}}{yes}{no}}
        add_header = X-Spam-Flag: YES
  deny  condition = \${if and{{def:spam_score_int}{>{\$spam_score_int}{150}}}{yes}{no}}
        message = Message rejected as spam
  accept

begin routers

hostpanel_aliases:
  driver = redirect
  domains = +local_domains
  data = \${lookup{\$local_part@\$domain}lsearch{/etc/postfix/valias}{\$value}fail}
  allow_defer
  allow_fail
  check_ancestor

hostpanel_catchall:
  driver = redirect
  domains = +local_domains
  data = \${lookup{@\$domain}lsearch{/etc/postfix/valias}{\$value}fail}
  allow_defer
  allow_fail

hostpanel_mailboxes:
  driver = accept
  domains = +local_domains
  condition = \${if !eq{\${lookup{\$local_part@\$domain}lsearch{/etc/postfix/vmailbox}{\$value}{}}}{}{yes}{no}}
  transport = dovecot_lmtp
  cannot_route_message = Unknown mailbox

remote_dnslookup:
  driver = dnslookup
  domains = ! +local_domains
  transport = remote_smtp
  ignore_target_hosts = 0.0.0.0 : 127.0.0.0/8 : ::1
  no_more

begin transports

remote_smtp:
  driver = smtp
  interface = \${lookup{\${lc:\$sender_address_domain}}lsearch{$EXIM_CONF_DIR/hostpanel-source-ip}{\$value}{}}
  dkim_domain = \${if exists{/etc/opendkim/keys/\${lc:\$sender_address_domain}/mail.private}{\${lc:\$sender_address_domain}}{}}
  dkim_selector = mail
  dkim_private_key = \${if exists{/etc/opendkim/keys/\${lc:\$sender_address_domain}/mail.private}{/etc/opendkim/keys/\${lc:\$sender_address_domain}/mail.private}{0}}
  dkim_canon = relaxed
  dkim_strict = false

# The selected Exim package includes the LMTP transport used here.
dovecot_lmtp:
  driver = lmtp
  socket = /var/run/dovecot/lmtp
  batch_max = 200

begin retry

*  *  F,2h,15m; G,16h,1h,1.5; F,4d,6h

begin rewrite

begin authenticators

dovecot_plain:
  driver = dovecot
  public_name = PLAIN
  server_socket = /var/run/dovecot/auth-client
  server_set_id = \$auth1
  server_advertise_condition = \${if def:tls_in_cipher}

dovecot_login:
  driver = dovecot
  public_name = LOGIN
  server_socket = /var/run/dovecot/auth-client
  server_set_id = \$auth1
  server_advertise_condition = \${if def:tls_in_cipher}
EOF

  cat >/etc/dovecot/conf.d/99-hostpanel.conf <<EOF
# Managed by HostPanel — Exim profile
mail_location = maildir:$MAIL_ROOT/%d/%n/Maildir
passdb { driver = passwd-file; args = /etc/dovecot/users }
userdb { driver = passwd-file; args = /etc/dovecot/users }
protocols = imap lmtp
ssl = required
ssl_min_protocol = TLSv1.2
ssl_cert = <$MAIL_TLS_CERT
ssl_key = <$MAIL_TLS_KEY
disable_plaintext_auth = yes
auth_mechanisms = plain login
service auth {
  unix_listener auth-client {
    mode = 0660
    user = $EXIM_GROUP
    group = $EXIM_GROUP
  }
}
service lmtp {
  unix_listener lmtp {
    mode = 0660
    user = $EXIM_GROUP
    group = $EXIM_GROUP
  }
}
EOF
  if [[ "$PKG_FAMILY" == debian ]]; then
    update-exim4.conf >>"$LOG" 2>&1 || die "Could not generate Exim configuration"
  fi
  "$EXIM_BIN" -bV >>"$LOG" 2>&1 || die "Exim cannot start"
  "$EXIM_BIN" -bP >>"$LOG" 2>&1 || die "Exim configuration is invalid"
  MTA_UNIT="$(svc exim)"
fi

chown root:dovecot /etc/dovecot/users
chmod 640 /etc/dovecot/users
doveconf -n >>"$LOG" 2>&1 || die "Dovecot configuration is invalid"
systemctl disable --now "$( [[ "$MAIL_MTA" == exim ]] && echo postfix || svc exim )" >>"$LOG" 2>&1 || true
systemctl enable --now "$MTA_UNIT" dovecot >>"$LOG" 2>&1 || die "$MTA_UNIT or Dovecot failed to start"
ok "$MTA_UNIT submission and Dovecot authentication configured with TLS"
fi

if has_role mail; then
say "Configuring DKIM"
mkdir -p /etc/opendkim/keys
touch /etc/opendkim/KeyTable /etc/opendkim/SigningTable
if [[ "$MAIL_MTA" == postfix ]]; then
  cat >/etc/opendkim/TrustedHosts <<EOF
127.0.0.1
localhost
::1
EOF
  python3 - <<'PYDKIM'
from pathlib import Path
path=Path('/etc/opendkim.conf')
text=path.read_text() if path.exists() else ''
start='# BEGIN HostPanel managed block'
end='# END HostPanel managed block'
if start in text and end in text:
    before=text.split(start,1)[0].rstrip()
    after=text.split(end,1)[1].lstrip()
    text=before+'\n'+after
block='''# BEGIN HostPanel managed block
Canonicalization   relaxed/simple
Mode               sv
SubDomains         no
KeyTable           /etc/opendkim/KeyTable
SigningTable       refile:/etc/opendkim/SigningTable
ExternalIgnoreList /etc/opendkim/TrustedHosts
InternalHosts      /etc/opendkim/TrustedHosts
Socket             inet:8891@localhost
# END HostPanel managed block
'''
path.write_text(text.rstrip()+'\n\n'+block)
PYDKIM
  chown -R opendkim:opendkim /etc/opendkim
  postconf -e "milter_protocol = 6" >>"$LOG" 2>&1
  postconf -e "milter_default_action = accept" >>"$LOG" 2>&1
  postconf -e "smtpd_milters = inet:localhost:8891" >>"$LOG" 2>&1
  postconf -e "non_smtpd_milters = inet:localhost:8891" >>"$LOG" 2>&1
  systemctl enable --now opendkim >>"$LOG" 2>&1 || die "OpenDKIM failed to start"
  systemctl restart postfix >>"$LOG" 2>&1 || true
  ok "OpenDKIM signing enabled for Postfix"
else
  chown root:"${EXIM_GROUP:-$(getent group exim >/dev/null 2>&1 && echo exim || echo Debian-exim)}" /etc/opendkim /etc/opendkim/keys
  chmod 750 /etc/opendkim /etc/opendkim/keys
  chmod 640 /etc/opendkim/KeyTable /etc/opendkim/SigningTable
  systemctl disable --now opendkim >>"$LOG" 2>&1 || true
  systemctl reload "$(svc exim)" >>"$LOG" 2>&1 || true
  ok "native Exim DKIM signing enabled"
fi
fi

if [[ "${HP_ENABLE_FTP:-no}" == yes ]] && has_role web; then
say "Configuring FTP"
MAIL_TLS_KEY="${MAIL_TLS_KEY:-/etc/ssl/private/hostpanel-ftp.key}"
MAIL_TLS_CERT="${MAIL_TLS_CERT:-/etc/ssl/certs/hostpanel-ftp.crt}"
if [[ ! -s "$MAIL_TLS_KEY" || ! -s "$MAIL_TLS_CERT" ]]; then
  openssl req -x509 -newkey rsa:3072 -nodes -days 825 -keyout "$MAIL_TLS_KEY" -out "$MAIL_TLS_CERT" \
    -subj "/CN=$PANEL_HOST" -addext "subjectAltName=DNS:$PANEL_HOST" >>"$LOG" 2>&1 \
    || die "Could not create the FTP certificate"
  chmod 600 "$MAIL_TLS_KEY"; chmod 644 "$MAIL_TLS_CERT"
fi
rm -f /etc/vsftpd/virtual_users.txt
EMPTY_USERS="$(mktemp /tmp/hostpanel-empty-ftp.XXXXXX)"
: >"$EMPTY_USERS"
umask 077
/usr/bin/db_load -T -t hash -f "$EMPTY_USERS" /etc/vsftpd/virtual_users.db
rm -f "$EMPTY_USERS"
chown root:root /etc/vsftpd/virtual_users.db
chmod 600 /etc/vsftpd/virtual_users.db
cat >/etc/pam.d/vsftpd.virtual <<EOF
auth required pam_userdb.so db=/etc/vsftpd/virtual_users crypt=crypt
account required pam_userdb.so db=/etc/vsftpd/virtual_users
EOF
cat >/etc/vsftpd.conf <<EOF
# Managed by HostPanel
listen=NO
listen_ipv6=YES
anonymous_enable=NO
local_enable=YES
write_enable=YES
chroot_local_user=YES
allow_writeable_chroot=YES
guest_enable=YES
guest_username=ftp
virtual_use_local_privs=YES
user_sub_token=\$USER
pam_service_name=vsftpd.virtual
user_config_dir=/etc/vsftpd/users
pasv_min_port=40000
pasv_max_port=40100
ssl_enable=YES
force_local_data_ssl=YES
force_local_logins_ssl=YES
ssl_sslv2=NO
ssl_sslv3=NO
ssl_tlsv1=NO
ssl_tlsv1_1=NO
ssl_tlsv1_2=YES
require_ssl_reuse=NO
rsa_cert_file=$MAIL_TLS_CERT
rsa_private_key_file=$MAIL_TLS_KEY
EOF
fw_allow 21/tcp
fw_allow 40000:40100/tcp
systemctl restart vsftpd >>"$LOG" 2>&1 || die "vsftpd failed to start with mandatory TLS"
ok "vsftpd configured with salted hashes and mandatory TLS"

fi

if has_role mail; then
say "Configuring mail rules"
mkdir -p /var/mail/sieve
chown -R vmail:vmail /var/mail/sieve
install -d -o root -g root -m 755 /usr/lib/dovecot/sieve
cat >/usr/lib/dovecot/sieve/report-spam.sieve <<'EOF'
require ["vnd.dovecot.pipe", "copy", "imapsieve", "environment", "variables"];
if environment :matches "imap.user" "*" {
  set "username" "${1}";
}
pipe :copy "hostpanel-rspamc-spam" [ "${username}" ];
EOF
cat >/usr/lib/dovecot/sieve/report-ham.sieve <<'EOF'
require ["vnd.dovecot.pipe", "copy", "imapsieve", "environment", "variables"];
if environment :matches "imap.mailbox" "*" {
  set "mailbox" "${1}";
}
if string "${mailbox}" [ "Trash", "Junk", "Spam" ] {
  stop;
}
if environment :matches "imap.user" "*" {
  set "username" "${1}";
}
pipe :copy "hostpanel-rspamc-ham" [ "${username}" ];
EOF
cat >/usr/lib/dovecot/sieve/hostpanel-rspamc-spam <<'EOF'
#!/bin/sh
set -eu
mailbox=${1:-}
case "$mailbox" in
  ""|*[!A-Za-z0-9._%+@-]*) exit 64 ;;
esac
exec /usr/bin/rspamc -d "$mailbox" learn_spam
EOF
cat >/usr/lib/dovecot/sieve/hostpanel-rspamc-ham <<'EOF'
#!/bin/sh
set -eu
mailbox=${1:-}
case "$mailbox" in
  ""|*[!A-Za-z0-9._%+@-]*) exit 64 ;;
esac
exec /usr/bin/rspamc -d "$mailbox" learn_ham
EOF
chmod 755 /usr/lib/dovecot/sieve/hostpanel-rspamc-spam /usr/lib/dovecot/sieve/hostpanel-rspamc-ham
chmod 644 /usr/lib/dovecot/sieve/report-spam.sieve /usr/lib/dovecot/sieve/report-ham.sieve
/usr/bin/sievec /usr/lib/dovecot/sieve/report-spam.sieve
/usr/bin/sievec /usr/lib/dovecot/sieve/report-ham.sieve
cat >/etc/dovecot/conf.d/90-sieve-hostpanel.conf <<'EOF'
# Managed by HostPanel
namespace inbox {
  mailbox Junk {
    auto = create
    special_use = \Junk
  }
}
protocol imap { mail_plugins = $mail_plugins imap_sieve }
plugin {
  sieve = file:/var/mail/sieve/%d/%n.sieve
  sieve_vacation_default_period = 1d
  sieve_vacation_max_period = 60d
  sieve_plugins = sieve_imapsieve sieve_extprograms
  imapsieve_mailbox1_name = Junk
  imapsieve_mailbox1_causes = COPY
  imapsieve_mailbox1_before = file:/usr/lib/dovecot/sieve/report-spam.sieve
  imapsieve_mailbox2_name = *
  imapsieve_mailbox2_from = Junk
  imapsieve_mailbox2_causes = COPY
  imapsieve_mailbox2_before = file:/usr/lib/dovecot/sieve/report-ham.sieve
  sieve_pipe_bin_dir = /usr/lib/dovecot/sieve
  sieve_global_extensions = +vnd.dovecot.pipe +vnd.dovecot.environment
}
protocol lda { mail_plugins = $mail_plugins sieve }
protocol lmtp { mail_plugins = $mail_plugins sieve }
EOF
doveconf -n >>"$LOG" 2>&1 || die "Dovecot IMAPSieve configuration is invalid"
systemctl restart dovecot >>"$LOG" 2>&1 || true
ok "sieve rules, vacation replies and Junk-folder spam learning enabled"

fi

if has_role web; then
say "Configuring the web application firewall"
if [[ -f /usr/share/modsecurity-crs/crs-setup.conf.example ]]; then
  cp -n /usr/share/modsecurity-crs/crs-setup.conf.example \
        /usr/share/modsecurity-crs/crs-setup.conf 2>/dev/null || true
fi
mkdir -p /etc/hostpanel/waf
touch /var/log/modsec_audit.log
chown "$PANEL_USER:$PANEL_USER" /var/log/modsec_audit.log 2>/dev/null || true
# Deliberately not enabled for any domain: the core rule set produces false
# positives, and turning it on unattended breaks customer sites quietly.
ok "modsecurity available — enable per domain, starting in detection mode"

fi

if has_role web || has_role database || has_role mail; then
say "Configuring the cache"
# Redis ACLs are what keeps one customer's cache out of another's. Without an
# aclfile they live only in memory and vanish on restart, which would silently
# remove the separation rather than fail loudly.
REDIS_CONF=/etc/redis/redis.conf
[[ "$PKG_FAMILY" == rhel ]] && REDIS_CONF=/etc/redis.conf
if [[ -f "$REDIS_CONF" ]]; then
  REDIS_ACL_DIR="$(dirname "$REDIS_CONF")"
  REDIS_ACL="$REDIS_ACL_DIR/users.acl"
  touch "$REDIS_ACL"
  chown redis:redis "$REDIS_ACL"
  grep -q "^aclfile" "$REDIS_CONF" || echo "aclfile $REDIS_ACL" >>"$REDIS_CONF"
  grep -q "^maxmemory " "$REDIS_CONF" || {
    echo "maxmemory 256mb" >>"$REDIS_CONF"
    echo "maxmemory-policy allkeys-lru" >>"$REDIS_CONF"
  }
  systemctl restart "$(svc redis)" >>"$LOG" 2>&1 || true
  ok "redis configured with a persistent ACL file"
else
  ok "redis not present, skipped"
fi

fi

say "Configuring PostgreSQL"
if command -v psql >/dev/null; then
  if [[ "$PKG_FAMILY" == rhel && ! -s /var/lib/pgsql/data/PG_VERSION ]] && command -v postgresql-setup >/dev/null 2>&1; then
    postgresql-setup --initdb >>"$LOG" 2>&1 || true
  fi
  systemctl enable --now postgresql >>"$LOG" 2>&1 || true
  ok "postgresql running"
else
  ok "postgresql not present, skipped"
fi

if has_role mail; then
say "Configuring spam filtering"
mkdir -p /etc/rspamd/local.d/maps
cat >/etc/rspamd/local.d/actions.conf <<'EOF'
greylist = 4;
add_header = 6;
reject = 15;
EOF
touch /etc/rspamd/local.d/maps/allow_from /etc/rspamd/local.d/maps/block_from
cat >/etc/rspamd/local.d/multimap.conf <<'EOF'
ALLOW_SENDER {
  type = "from";
  map = "/etc/rspamd/local.d/maps/allow_from";
  score = -20.0;
}
BLOCK_SENDER {
  type = "from";
  map = "/etc/rspamd/local.d/maps/block_from";
  score = 20.0;
}
EOF
cat >/etc/rspamd/local.d/worker-proxy.inc <<'EOF'
milter = yes;
timeout = 120s;
upstream "local" { default = yes; self_scan = yes; }
bind_socket = "localhost:11332";
EOF
cat >/etc/rspamd/local.d/redis.conf <<'EOF'
servers = "127.0.0.1";
EOF
cat >/etc/rspamd/local.d/classifier-bayes.conf <<'EOF'
# Managed by HostPanel. Redis-backed, per-recipient Bayes with conservative
# automatic learning and balance protection.
backend = "redis";
new_schema = true;
per_user = true;
expire = 8640000;
autolearn {
  spam_threshold = 12.0;
  ham_threshold = -2.0;
  check_balance = true;
}
EOF
if id _rspamd >/dev/null 2>&1; then chown -R _rspamd:_rspamd /etc/rspamd/local.d; elif id rspamd >/dev/null 2>&1; then chown -R rspamd:rspamd /etc/rspamd/local.d; fi

systemctl enable --now "$(svc redis)" >>"$LOG" 2>&1 || true
if [[ "$MAIL_MTA" == postfix ]]; then
  # Postfix talks to Rspamd's milter proxy, then OpenDKIM signs outbound mail.
  postconf -e "smtpd_milters = inet:localhost:11332 inet:localhost:8891" >>"$LOG" 2>&1
  postconf -e "non_smtpd_milters = inet:localhost:8891" >>"$LOG" 2>&1
  systemctl restart rspamd postfix >>"$LOG" 2>&1 || true
else
  # Exim uses the spamd-compatible scanner interface on loopback.
  cat >/etc/rspamd/local.d/worker-normal.inc <<'EOF'
bind_socket = "127.0.0.1:11333";
EOF
  systemctl restart rspamd "$(svc exim)" >>"$LOG" 2>&1 || true
fi
ok "rspamd filtering inbound mail for $MAIL_MTA"

fi

if has_role web; then
say "Configuring Apache as a private backend"
if [[ -d "$APACHE_CONF_DIR" ]]; then
  cat >"$APACHE_PORTS" <<'EOF'
Listen 127.0.0.1:8080
EOF
  if [[ "$PKG_FAMILY" == debian ]]; then
    a2dissite 000-default >/dev/null 2>&1 || true
  else
    [[ -f /etc/httpd/conf.d/welcome.conf ]] && mv -f /etc/httpd/conf.d/welcome.conf /etc/httpd/conf.d/welcome.conf.disabled
    [[ -f /etc/httpd/conf.d/autoindex.conf ]] && mv -f /etc/httpd/conf.d/autoindex.conf /etc/httpd/conf.d/autoindex.conf.disabled
  fi
  # a2enmod exists only on Debian; RHEL ships these as always-loaded conf.modules.d
  # entries, so there is nothing to enable there.
  [[ "$PKG_FAMILY" == debian ]] && { a2enmod proxy proxy_fcgi remoteip rewrite setenvif >/dev/null 2>&1 || true; }
  if "$APACHE_CTL" configtest >>"$LOG" 2>&1; then systemctl restart "$(svc apache)" >>"$LOG" 2>&1 || true; fi
  ok "apache bound to 127.0.0.1:8080"
fi

fi

if has_role web; then
say "Configuring WebDAV, calendars and contacts"
mkdir -p /etc/hostpanel/cpanel /etc/hostpanel/webdav-auth /var/lib/radicale/collections
printf '[]\n' >/etc/hostpanel/cpanel/webdav.json
chmod 600 /etc/hostpanel/cpanel/webdav.json
chown "$PANEL_USER:$PANEL_USER" /etc/hostpanel/cpanel/webdav.json
if id radicale >/dev/null 2>&1; then
  chown -R radicale:radicale /var/lib/radicale
elif id _radicale >/dev/null 2>&1; then
  chown -R _radicale:_radicale /var/lib/radicale
fi
HP_DB="$PANEL_DIR/hostpanel.db" "$PANEL_DIR/app/hostpanel-webdav-sync" >>"$LOG" 2>&1 || true
HP_DB="$PANEL_DIR/hostpanel.db" "$PANEL_DIR/app/hostpanel-calendar-sync" >>"$LOG" 2>&1 || true
ok "WebDAV and CalDAV/CardDAV backends configured on loopback"

fi

if has_role web; then
say "Configuring OpenLiteSpeed as a private per-domain backend"
OLS_ROOT=/usr/local/lsws
if [[ -x "$OLS_ROOT/bin/openlitespeed" ]]; then
  systemctl stop lsws >>"$LOG" 2>&1 || true
  mkdir -p "$OLS_ROOT/conf/hostpanel" "$OLS_ROOT/conf/vhosts" \
           /etc/hostpanel/openlitespeed/domains /var/log/hostpanel/openlitespeed

  python3 - <<'PYOLS'
from pathlib import Path
import re

main = Path('/usr/local/lsws/conf/httpd_config.conf')
if not main.exists():
    raise SystemExit('OpenLiteSpeed plain-text configuration was not found')
text = main.read_text()
# The package example must not remain publicly reachable. HostPanel gets its
# own loopback listener on 8088; the example is retained only on localhost:8099.
text = re.sub(r'(?m)^(\s*address\s+)\*:8088\s*$', r'\g<1>127.0.0.1:8099', text)
if not re.search(r'(?m)^\s*useIpInProxyHeader\s+', text):
    text += '\nuseIpInProxyHeader 1\n'
include = 'include $SERVER_ROOT/conf/hostpanel/*.conf'
if include not in text:
    text += f'\n# HostPanel managed virtual hosts\n{include}\n'
main.write_text(text)

admin = Path('/usr/local/lsws/admin/conf/admin_config.conf')
if admin.exists():
    value = admin.read_text()
    value = re.sub(r'(?m)^(\s*address\s+)\*:7080\s*$', r'\g<1>127.0.0.1:7080', value)
    admin.write_text(value)
PYOLS

  cat >"$OLS_ROOT/conf/hostpanel/hostpanel.conf" <<'EOF'
# Managed by HostPanel — domains are added by app/hostpanel-root
listener HostPanel {
  address 127.0.0.1:8088
  secure 0
}
EOF
  chown -R root:lsadm "$OLS_ROOT/conf/hostpanel" "$OLS_ROOT/conf/vhosts" 2>/dev/null || true
  chmod 750 /etc/hostpanel/openlitespeed /etc/hostpanel/openlitespeed/domains
  chown -R root:"$PANEL_USER" /etc/hostpanel/openlitespeed
  "$OLS_ROOT/bin/openlitespeed" -t >>"$LOG" 2>&1 \
    || die "OpenLiteSpeed rejected the HostPanel configuration"
  systemctl enable --now lsws >>"$LOG" 2>&1 \
    || die "OpenLiteSpeed failed to start"
  ok "OpenLiteSpeed listening only on 127.0.0.1:8088; WebAdmin only on localhost:7080"
else
  die "OpenLiteSpeed binary is missing after installation"
fi

fi

if has_role web && has_role mail; then
say "Installing verified Roundcube webmail"
ROUNDCUBE_VERSION="${HP_ROUNDCUBE_VERSION:-1.7.2}"
case "$ROUNDCUBE_VERSION" in
  1.7.2) ROUNDCUBE_SHA256="01bf9ede1665e507db94bab1361ebed20ee353dba04bc628b00fb6eca05af3d1" ;;
  *) die "Unsupported HP_ROUNDCUBE_VERSION=$ROUNDCUBE_VERSION; add a reviewed checksum before changing versions" ;;
esac
ROUNDCUBE_TARGET=/usr/share/roundcube
ensure_bootstrap_temp_dir
ROUNDCUBE_ARCHIVE="$BOOTSTRAP_TEMP_DIR/roundcubemail-${ROUNDCUBE_VERSION}-complete.tar.gz"
if [[ ! -f "$ROUNDCUBE_TARGET/.hostpanel-version" ]] \
   || [[ "$(cat "$ROUNDCUBE_TARGET/.hostpanel-version" 2>/dev/null || true)" != "$ROUNDCUBE_VERSION" ]] \
   || [[ ! -f "$ROUNDCUBE_TARGET/public_html/index.php" ]]; then
  curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
    "https://github.com/roundcube/roundcubemail/releases/download/${ROUNDCUBE_VERSION}/roundcubemail-${ROUNDCUBE_VERSION}-complete.tar.gz" \
    -o "$ROUNDCUBE_ARCHIVE" || die "Could not download Roundcube $ROUNDCUBE_VERSION"
  printf '%s  %s\n' "$ROUNDCUBE_SHA256" "$ROUNDCUBE_ARCHIVE" | sha256sum -c - >>"$LOG" 2>&1 \
    || die "Roundcube checksum verification failed"
  ROUNDCUBE_STAGE="$(mktemp -d /tmp/hostpanel-roundcube.XXXXXX)"
  python3 - "$ROUNDCUBE_ARCHIVE" "$ROUNDCUBE_STAGE" <<'PYROUNDCUBE'
import pathlib, sys, tarfile
archive, target = sys.argv[1:]
with tarfile.open(archive, "r:gz") as bundle:
    members = bundle.getmembers()
    if not members or len(members) > 20000:
        raise SystemExit("unsafe Roundcube archive member count")
    total = 0
    for member in members:
        parts = pathlib.PurePosixPath(member.name).parts
        if member.name.startswith("/") or ".." in parts or not (member.isfile() or member.isdir()):
            raise SystemExit(f"unsafe Roundcube archive member: {member.name}")
        total += max(0, member.size)
        if member.size > 64 * 1024 * 1024 or total > 512 * 1024 * 1024:
            raise SystemExit("Roundcube archive expands beyond its safety limit")
    bundle.extractall(target, members=members, filter="data")
PYROUNDCUBE
  ROUNDCUBE_SOURCE="$(find "$ROUNDCUBE_STAGE" -mindepth 1 -maxdepth 1 -type d -name "roundcubemail-*" -print -quit)"
  [[ -n "$ROUNDCUBE_SOURCE" && -f "$ROUNDCUBE_SOURCE/public_html/index.php" \
      && -f "$ROUNDCUBE_SOURCE/SQL/sqlite.initial.sql" ]] \
    || die "Roundcube archive has an unexpected layout"
  ROUNDCUBE_OLD_CONFIG=""
  if [[ -f "$ROUNDCUBE_TARGET/config/config.inc.php" ]]; then
    ROUNDCUBE_OLD_CONFIG="$(mktemp /tmp/hostpanel-roundcube-config.XXXXXX)"
    cp -a "$ROUNDCUBE_TARGET/config/config.inc.php" "$ROUNDCUBE_OLD_CONFIG"
  fi
  rm -rf "$ROUNDCUBE_TARGET"
  mv "$ROUNDCUBE_SOURCE" "$ROUNDCUBE_TARGET"
  [[ -z "$ROUNDCUBE_OLD_CONFIG" ]] || cp -a "$ROUNDCUBE_OLD_CONFIG" "$ROUNDCUBE_TARGET/config/config.inc.php"
  rm -rf "$ROUNDCUBE_STAGE" "$ROUNDCUBE_ARCHIVE"
  [[ -z "$ROUNDCUBE_OLD_CONFIG" ]] || rm -f "$ROUNDCUBE_OLD_CONFIG"
fi
mkdir -p /var/lib/roundcube "$ROUNDCUBE_TARGET/temp" "$ROUNDCUBE_TARGET/logs" "$ROUNDCUBE_TARGET/config"
if [[ ! -s /var/lib/roundcube/roundcube.sqlite ]]; then
  sqlite3 /var/lib/roundcube/roundcube.sqlite <"$ROUNDCUBE_TARGET/SQL/sqlite.initial.sql" \
    || die "Could not initialise the Roundcube database"
fi
if [[ ! -s "$ROUNDCUBE_TARGET/config/config.inc.php" ]]; then
  ROUNDCUBE_DES_KEY="$(openssl rand -hex 24)"
  cat >"$ROUNDCUBE_TARGET/config/config.inc.php" <<EOF
<?php
// Managed by HostPanel. Local loopback services use the server's own TLS
// certificate, so certificate verification is disabled only for loopback.
\$config = [];
\$config['db_dsnw'] = 'sqlite:////var/lib/roundcube/roundcube.sqlite?mode=0660';
\$config['imap_host'] = 'tls://127.0.0.1:143';
\$config['smtp_host'] = 'tls://127.0.0.1:587';
\$config['smtp_user'] = '%u';
\$config['smtp_pass'] = '%p';
\$config['imap_conn_options'] = ['ssl' => ['verify_peer' => false, 'verify_peer_name' => false]];
\$config['smtp_conn_options'] = ['ssl' => ['verify_peer' => false, 'verify_peer_name' => false]];
\$config['des_key'] = '$ROUNDCUBE_DES_KEY';
\$config['product_name'] = 'HostPanel Webmail';
\$config['plugins'] = ['archive', 'zipdownload', 'managesieve', 'markasjunk'];
\$config['junk_mbox'] = 'Junk';
\$config['enable_installer'] = false;
EOF
fi
printf '%s\n' "$ROUNDCUBE_VERSION" >"$ROUNDCUBE_TARGET/.hostpanel-version"
rm -rf "$ROUNDCUBE_TARGET/installer" "$ROUNDCUBE_TARGET/public_html/installer.php"
chown -R root:root "$ROUNDCUBE_TARGET"
find "$ROUNDCUBE_TARGET" -type d -exec chmod 755 {} +
find "$ROUNDCUBE_TARGET" -type f -exec chmod 644 {} +
chown root:"$WEB_USER" "$ROUNDCUBE_TARGET/config/config.inc.php"
chmod 640 "$ROUNDCUBE_TARGET/config/config.inc.php"
chown -R "$WEB_USER":"$WEB_USER" "$ROUNDCUBE_TARGET/temp" "$ROUNDCUBE_TARGET/logs"
chmod 770 "$ROUNDCUBE_TARGET/temp" "$ROUNDCUBE_TARGET/logs"
chown -R root:"$WEB_USER" /var/lib/roundcube
chmod 770 /var/lib/roundcube
chmod 660 /var/lib/roundcube/roundcube.sqlite
ok "Roundcube $ROUNDCUBE_VERSION installed from a verified complete release"

say "Configuring webmail"
ROUNDCUBE_ROOT=""
for candidate in /usr/share/roundcubemail/public_html /usr/share/roundcube/public_html /usr/share/roundcubemail /usr/share/roundcube; do
  [[ -f "$candidate/index.php" ]] && { ROUNDCUBE_ROOT="$candidate"; break; }
done
if [[ -n "$ROUNDCUBE_ROOT" ]]; then
  PHP_SOCKET="$(find /run/php -maxdepth 1 -type s -name 'php*-fpm.sock' | sort -V | tail -1)"
  [[ -n "$PHP_SOCKET" ]] || PHP_SOCKET=/run/php/php-fpm.sock
  cat >"$NGINX_CONF_DIR/webmail$VHOST_EXT" <<'EOF'
server {
    listen 80;
    server_name webmail.*;
    root __ROUNDCUBE_ROOT__;
    index index.php;

    location / { try_files $uri $uri/ /index.php?$query_string; }
    location ~ \.php$ {
        include fastcgi_params;
        fastcgi_param SCRIPT_FILENAME $document_root$fastcgi_script_name;
        fastcgi_pass unix:__PHP_SOCKET__;
    }
    location ~ ^/(config|temp|logs)(/|$) { deny all; }
}
EOF
  sed -i "s#__PHP_SOCKET__#$PHP_SOCKET#g; s#__ROUNDCUBE_ROOT__#$ROUNDCUBE_ROOT#g" "$NGINX_CONF_DIR/webmail$VHOST_EXT"
  # Only Debian needs the link; on RHEL the file is already in the read directory.
  [[ "$NGINX_CONF_DIR" != "$NGINX_LINK_DIR" ]] && ln -sf "$NGINX_CONF_DIR/webmail" "$NGINX_LINK_DIR/webmail"
  nginx -t >>"$LOG" 2>&1 || die "nginx rejected the Roundcube configuration"
  systemctl reload nginx >>"$LOG" 2>&1 || die "nginx could not reload the Roundcube configuration"
  ok "roundcube served on webmail.<yourdomain>"
else
  die "Roundcube was selected but no application root was installed"
fi

fi

say "Granting the panel controlled root actions"
# The panel runs unprivileged. These are the only root commands it may run.
cat >/etc/sudoers.d/hostpanel <<EOF
# HostPanel has one privileged entry point. Every operation and argument is
# validated again by this root-owned gateway; no wildcard command rules exist.
Defaults!$PANEL_DIR/app/hostpanel-root env_reset,secure_path=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
$PANEL_USER ALL=(root) NOPASSWD: $PANEL_DIR/app/hostpanel-root
EOF
chmod 440 /etc/sudoers.d/hostpanel

# The application tree is executable code and therefore entirely root-owned.
# The panel account receives read/execute access only; mutable state lives in
# the database, backup directory and narrowly scoped /etc/hostpanel data dirs.
visudo -cf /etc/sudoers.d/hostpanel >>"$LOG" 2>&1 || die "sudoers file is invalid — nothing was applied"

mkdir -p /etc/hostpanel/git/keys /etc/hostpanel/sites /etc/hostpanel/appservers \
         /etc/hostpanel/waf /etc/hostpanel/certs /etc/hostpanel/cpanel \
         /etc/hostpanel/staging /etc/hostpanel/expansion /etc/hostpanel/expansion-agent \
         /etc/hostpanel/mail-convenience/retention "$PANEL_DIR/plugins" \
         /var/lib/hostpanel/quarantine /var/lib/hostpanel/expansion /var/lib/hostpanel/expansion-agent /var/lib/hostpanel/app-packages
chown root:root "$PANEL_DIR"
chmod 755 "$PANEL_DIR"
chown -R root:root "$PANEL_DIR/app" "$PANEL_DIR/ops" "$PANEL_DIR/venvs" "$PANEL_DIR/plugins"
find "$PANEL_DIR/app" -type d -exec chmod 755 {} +
find "$PANEL_DIR/app" -type f -exec chmod 644 {} +
find "$PANEL_DIR/ops" -type d -exec chmod 755 {} +
find "$PANEL_DIR/ops" -type f -exec chmod 755 {} +
while IFS= read -r worker; do
  [[ -z "$worker" || "$worker" == \#* ]] && continue
  [[ -f "$PANEL_DIR/app/$worker" ]] || die "privileged file listed but missing: $worker"
  chmod 755 "$PANEL_DIR/app/$worker"
done <"$PANEL_DIR/app/privileged-files.txt"
chown -R root:root "$PANEL_DIR/sdk" "$PANEL_DIR/tools" "$PANEL_DIR/releases" "$PANEL_DIR/integrations" 2>/dev/null || true
chown root:root "$PANEL_DIR/VERSION" "$PANEL_DIR/requirements.lock"
chmod 644 "$PANEL_DIR/VERSION" "$PANEL_DIR/requirements.lock"

# Tenant homes keep their tenant ownership across reinstalls.
chown root:root "$VHOST_ROOT"
chmod 755 "$VHOST_ROOT"
chown -R "$PANEL_USER:$PANEL_USER" "$BACKUP_DIR"
chmod 750 "$BACKUP_DIR"

# Keep the /etc/hostpanel parent root-owned. A panel-owned parent could rename
# a protected child directory and replace it with attacker-controlled content.
chown root:"$PANEL_USER" /etc/hostpanel
chmod 750 /etc/hostpanel

# The first-party expansion agent uses a bearer token whose plaintext is only
# exposed to root on the enrolled node.  The panel process can read the hash,
# never the token itself. Existing enrollment survives upgrades.
if [[ ! -s /etc/hostpanel/expansion-agent-token.sha256 ]]; then
  EXPANSION_AGENT_TOKEN="$(random_alnum 64)"
  printf '%s' "$EXPANSION_AGENT_TOKEN" | sha256sum | awk '{print $1}' >/etc/hostpanel/expansion-agent-token.sha256
  printf '%s\n' "$EXPANSION_AGENT_TOKEN" >/root/.hostpanel-expansion-agent-token
  chmod 600 /root/.hostpanel-expansion-agent-token
  ok "expansion agent token created at /root/.hostpanel-expansion-agent-token"
fi
chown root:"$PANEL_USER" /etc/hostpanel/expansion-agent-token.sha256
chmod 640 /etc/hostpanel/expansion-agent-token.sha256

# Only metadata that is not parsed by a root service is writable by the panel.
for mutable in /etc/hostpanel/git /etc/hostpanel/appservers /etc/hostpanel/cpanel /etc/hostpanel/staging; do
  mkdir -p "$mutable"
  chown -R "$PANEL_USER:$PANEL_USER" "$mutable"
  find "$mutable" -type d -exec chmod 750 {} +
  find "$mutable" -type f -exec chmod 600 {} +
done

# Files parsed by root services are never writable by the panel account. They
# are created atomically through hostpanel-root or by a root-owned worker.
mkdir -p /etc/hostpanel/sites /etc/hostpanel/waf /etc/hostpanel/certs \
         /etc/hostpanel/openlitespeed/domains /etc/hostpanel/mail-policy \
         /etc/hostpanel/migration-keys /etc/hostpanel/suspended /etc/hostpanel/webdav-auth \
         /etc/hostpanel/expansion /etc/hostpanel/expansion-agent /etc/hostpanel/mail-convenience/retention
chown -R root:root /etc/hostpanel/sites /etc/hostpanel/waf /etc/hostpanel/certs \
         /etc/hostpanel/mail-policy /etc/hostpanel/migration-keys \
         /etc/hostpanel/suspended /etc/hostpanel/webdav-auth \
         /etc/hostpanel/expansion /etc/hostpanel/expansion-agent /etc/hostpanel/mail-convenience
find /etc/hostpanel/expansion /etc/hostpanel/expansion-agent /etc/hostpanel/mail-convenience -type d -exec chmod 750 {} +
find /etc/hostpanel/expansion /etc/hostpanel/expansion-agent /etc/hostpanel/mail-convenience -type f -exec chmod 640 {} +
find /etc/hostpanel/sites /etc/hostpanel/waf /etc/hostpanel/certs \
     /etc/hostpanel/mail-policy /etc/hostpanel/migration-keys \
     /etc/hostpanel/suspended /etc/hostpanel/webdav-auth -type d -exec chmod 755 {} +
find /etc/hostpanel/sites /etc/hostpanel/waf /etc/hostpanel/certs \
     /etc/hostpanel/mail-policy /etc/hostpanel/migration-keys \
     /etc/hostpanel/suspended /etc/hostpanel/webdav-auth -type f -exec chmod 640 {} +
find /etc/hostpanel/sites -type f -name 'htpasswd*' -exec chown "root:$WEB_USER" {} + -exec chmod 640 {} + 2>/dev/null || true
find /etc/hostpanel/webdav-auth -type f -exec chown "root:$WEB_USER" {} + -exec chmod 640 {} + 2>/dev/null || true
find /etc/hostpanel/certs -type f -name 'privkey.pem' -exec chmod 600 {} +
chown -R root:"$PANEL_USER" /etc/hostpanel/openlitespeed
find /etc/hostpanel/openlitespeed -type d -exec chmod 750 {} +
find /etc/hostpanel/openlitespeed -type f -exec chmod 640 {} +
chown root:"$PANEL_USER" /etc/hostpanel/alerts.conf /etc/hostpanel/offsite.conf 2>/dev/null || true
chmod 640 /etc/hostpanel/alerts.conf /etc/hostpanel/offsite.conf 2>/dev/null || true
chown root:root /etc/hostpanel/update.pub 2>/dev/null || true
chown root:"$PANEL_USER" /etc/hostpanel/node.secret 2>/dev/null || true
chmod 640 /etc/hostpanel/node.secret 2>/dev/null || true
chown -R root:"$PANEL_USER" /etc/hostpanel/node-secrets 2>/dev/null || true
find /etc/hostpanel/node-secrets -type d -exec chmod 750 {} + 2>/dev/null || true
find /etc/hostpanel/node-secrets -type f -name '*.secret' -exec chmod 640 {} + 2>/dev/null || true
chmod 644 /etc/hostpanel/update.pub 2>/dev/null || true
chown root:root "$PANEL_DIR/plugins" /var/lib/hostpanel/quarantine
chmod 755 "$PANEL_DIR/plugins" /var/lib/hostpanel/quarantine

chown root:"$PANEL_USER" "$PANEL_DIR/config.env"
chmod 640 "$PANEL_DIR/config.env"
chown -R root:"$PANEL_USER" "$PANEL_DIR/credentials"
chmod 750 "$PANEL_DIR/credentials"
chmod 640 "$PANEL_DIR/credentials"/*
chown "$PANEL_USER:$PANEL_USER" "$PANEL_DIR/hostpanel.db"
chmod 600 "$PANEL_DIR/hostpanel.db" 2>/dev/null || true

if [[ "$PKG_FAMILY" == rhel ]]; then
  say "Applying SELinux policy for HostPanel"
  [[ -x "$PANEL_DIR/ops/hostpanel-selinux" ]] || die "SELinux helper is missing"
  "$PANEL_DIR/ops/hostpanel-selinux" apply >>"$LOG" 2>&1 \
    || die "SELinux policy could not be applied; inspect $LOG"
  ok "SELinux contexts, booleans and panel port configured"
fi
ok "single-gateway sudo policy and root-owned application tree verified"

# --------------------------------------------------------------------------- #
say "Generating a TLS certificate for the panel"
mkdir -p "$PANEL_DIR/tls"
if [[ ! -f "$PANEL_DIR/tls/panel.crt" ]]; then
  openssl req -x509 -newkey rsa:2048 -nodes -days 825 \
    -keyout "$PANEL_DIR/tls/panel.key" \
    -out "$PANEL_DIR/tls/panel.crt" \
    -subj "/CN=$(hostname -f)" \
    -addext "subjectAltName=DNS:$(hostname -f),IP:$(hostname -I | awk '{print $1}')" \
    >>"$LOG" 2>&1
fi
chmod 600 "$PANEL_DIR/tls/panel.key"
chown -R "$PANEL_USER:$PANEL_USER" "$PANEL_DIR/tls"
command -v restorecon >/dev/null 2>&1 && restorecon -RF "$PANEL_DIR/tls" >/dev/null 2>&1 || true
ok "self-signed certificate created — replace it with a real one once you have a hostname"

say "Registering the systemd service"
# Bootstrap must be able to persist the one-time administrator credential before
# the service emits any success message.  Prepare the writable state parent
# before the first service start; narrower root-owned children are created later.
mkdir -p /var/lib/hostpanel
chown "$PANEL_USER:$PANEL_USER" /var/lib/hostpanel
chmod 750 /var/lib/hostpanel
SERVICE_AFTER="network-online.target"
has_role database && SERVICE_AFTER+=" mariadb.service postgresql.service"
cat >/etc/systemd/system/hostpanel.service <<EOF
[Unit]
Description=HostPanel node service
After=$SERVICE_AFTER
Wants=network-online.target

[Service]
User=$PANEL_USER
Group=$PANEL_USER
EnvironmentFile=$PANEL_DIR/config.env
LoadCredential=hp-master.key:$PANEL_DIR/credentials/master.key
LoadCredential=database-url:$PANEL_DIR/credentials/database-url
WorkingDirectory=$PANEL_DIR/app
ExecStart=$PANEL_DIR/venv/bin/uvicorn main:app --host 127.0.0.1 --port $PANEL_BACKEND_PORT \
  --proxy-headers --forwarded-allow-ips=127.0.0.1 \
  --limit-concurrency 192 --backlog 256 --timeout-keep-alive 5
Restart=always
RestartSec=3
UMask=0077
NoNewPrivileges=false
PrivateTmp=true
PrivateDevices=true
ProtectHome=read-only
ProtectSystem=full
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectKernelLogs=true
ProtectControlGroups=true
RestrictSUIDSGID=true
LockPersonality=true
RestrictRealtime=true
SystemCallArchitectures=native
ReadWritePaths=$VHOST_ROOT $BACKUP_DIR /etc/hostpanel /var/lib/hostpanel /var/log/hostpanel-install.log /var/log/hostpanel-proxy-traffic.log

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
mkdir -p /var/lib/hostpanel /var/lib/hostpanel/migrations /var/lib/hostpanel/root-work
chown "$PANEL_USER:$PANEL_USER" /var/lib/hostpanel /var/lib/hostpanel/migrations
chown root:root /var/lib/hostpanel/root-work
chmod 700 /var/lib/hostpanel/migrations /var/lib/hostpanel/root-work
systemctl enable --now hostpanel >>"$LOG" 2>&1
sleep 2
systemctl is-active --quiet hostpanel || die "Panel failed to start. Check: journalctl -u hostpanel -n 50"
READINESS_OK=no
for attempt in 1 2 3 4 5 6 7 8 9 10; do
  READINESS_RESPONSE="$(curl --silent --show-error --fail --max-time 5 \
    -H "x-hostpanel-readiness: $READINESS_TOKEN" \
    "http://127.0.0.1:$PANEL_BACKEND_PORT/api/internal/readiness" \
    2>>"$LOG" || true)"
  if [[ "$READINESS_RESPONSE" == *'"ready":true'* \
     || "$READINESS_RESPONSE" == *'"ready": true'* ]]; then
    READINESS_OK=yes
    break
  fi
  sleep 1
done
[[ "$READINESS_OK" == yes ]] \
  || die "Panel service started but its authenticated readiness check failed. Check: journalctl -u hostpanel -n 50"
ok "hostpanel.service running and authenticated readiness check passed"

say "Enabling the anti-DDoS edge"
[[ -x "$PANEL_DIR/ops/hostpanel-antiddos" ]] || die "anti-DDoS helper is missing"
HP_PANEL_DIR="$PANEL_DIR" HP_PANEL_PORT="$PANEL_PORT" \
  HP_PANEL_BACKEND_PORT="$PANEL_BACKEND_PORT" \
  "$PANEL_DIR/ops/hostpanel-antiddos" apply >>"$LOG" 2>&1 \
  || die "Anti-DDoS edge failed to activate. Inspect $LOG"
ok "nginx edge, loopback application backend and admission controls active"

# --------------------------------------------------------------------------- #
if has_role web; then
say "Enabling filesystem disk quotas"
# Quotas belong to the filesystem that actually stores customer files.  A
# dedicated /home/vhosts mount is common on production servers and may differ
# from /.  Detect it with findmnt -T rather than assuming the root filesystem.
QUOTA_TARGET="$VHOST_ROOT"
QUOTA_MOUNT="$(findmnt -no TARGET -T "$QUOTA_TARGET" 2>/dev/null || echo /)"
QUOTA_FS="$(findmnt -no FSTYPE -T "$QUOTA_TARGET" 2>/dev/null || echo unknown)"
QUOTA_OPTIONS="$(findmnt -no OPTIONS -T "$QUOTA_TARGET" 2>/dev/null || true)"
QUOTA_STATE="not enabled"
case "$QUOTA_FS" in
  ext4|ext3|ext2)
    if [[ ",$QUOTA_OPTIONS," != *,usrquota,* ]]; then
      if [[ "$QUOTA_MOUNT" == "/" ]]; then
        # Add usrquota to the root entry without disturbing anything else.
        cp /etc/fstab /etc/fstab.hostpanel.bak
        awk '$2=="/" && $4 !~ /(^|,)usrquota(,|$)/ {$4=$4",usrquota"} {print}' OFS='\t' \
          /etc/fstab.hostpanel.bak >/etc/fstab
        QUOTA_STATE="configured for $QUOTA_MOUNT in fstab — reboot, then run: quotacheck -cugm $QUOTA_MOUNT && quotaon $QUOTA_MOUNT"
      else
        QUOTA_STATE="$QUOTA_MOUNT needs usrquota in its mount options — update fstab and remount"
      fi
    else
      quotacheck -cugm "$QUOTA_MOUNT" >>"$LOG" 2>&1 || true
      quotaon "$QUOTA_MOUNT" >>"$LOG" 2>&1 || true
      QUOTA_STATE="active on $QUOTA_MOUNT"
    fi
    ;;
  xfs)
    if [[ ",$QUOTA_OPTIONS," != *,prjquota,* && ",$QUOTA_OPTIONS," != *,pquota,* ]]; then
      QUOTA_STATE="XFS mount $QUOTA_MOUNT needs prjquota in its mount options — update fstab and remount"
    else
      touch /etc/projects /etc/projid
      QUOTA_STATE="active on $QUOTA_MOUNT (XFS project quotas)"
    fi
    ;;
  *)
    QUOTA_STATE="unsupported filesystem ($QUOTA_FS on $QUOTA_MOUNT) — usage is measured, not enforced"
    ;;
esac
ok "disk quotas: $QUOTA_STATE"

else
  QUOTA_STATE="not applicable on a node without the web role"
  ok "disk quotas: $QUOTA_STATE"
fi

if has_role backup; then
  say "Scheduling nightly backups"
  chmod +x "$PANEL_DIR/app/hostpanel-backup"
  cat >/etc/cron.d/hostpanel-backup <<EOF
# Nightly verified backup at 03:00, pruning archives older than 14 days.
SHELL=/bin/bash
0 3 * * * root $PANEL_DIR/venv/bin/python $PANEL_DIR/app/hostpanel-backup >> /var/log/hostpanel-backup.log 2>&1
30 3 * * * root $PANEL_DIR/venv/bin/python $PANEL_DIR/app/hostpanel-backup --prune 14 >> /var/log/hostpanel-backup.log 2>&1
EOF
  chmod 644 /etc/cron.d/hostpanel-backup
  touch /var/log/hostpanel-backup.log
else
  rm -f /etc/cron.d/hostpanel-backup
  ok "nightly backup scheduler omitted on a node without the backup role"
fi

chmod +x "$PANEL_DIR/app/hostpanel-maint" "$PANEL_DIR/app/hostpanel-doctor" "$PANEL_DIR/app/hostpanel-update"
cat >/etc/cron.d/hostpanel-maint <<EOF
# Hourly: count bandwidth from the nginx logs and drop expired sessions.
# Nightly: push plan disk limits down to the filesystem.
SHELL=/bin/bash
17 * * * * root $PANEL_DIR/venv/bin/python $PANEL_DIR/app/hostpanel-maint >> /var/log/hostpanel-maint.log 2>&1
27 * * * * root $PANEL_DIR/venv/bin/python $PANEL_DIR/app/hostpanel-maint --stats >> /var/log/hostpanel-maint.log 2>&1
45 4 * * * root $PANEL_DIR/venv/bin/python $PANEL_DIR/app/hostpanel-maint --quotas >> /var/log/hostpanel-maint.log 2>&1
0 6 * * 1 root $PANEL_DIR/venv/bin/python $PANEL_DIR/app/hostpanel-doctor --quiet >> /var/log/hostpanel-doctor.log 2>&1
*/5 * * * * root $PANEL_DIR/venv/bin/python $PANEL_DIR/app/hostpanel-maint --recover-services >> /var/log/hostpanel-maint.log 2>&1
*/30 * * * * root $PANEL_DIR/venv/bin/python $PANEL_DIR/app/hostpanel-maint --alerts >> /var/log/hostpanel-maint.log 2>&1
EOF
chmod 644 /etc/cron.d/hostpanel-maint
touch /var/log/hostpanel-maint.log
chmod +x "$PANEL_DIR/app/hostpanel-cpanel-maint"
if has_role control; then
  cat >/etc/cron.d/hostpanel-cpanel <<EOF
SHELL=/bin/bash
*/2 * * * * root HP_DB=$PANEL_DIR/hostpanel.db HP_DATABASE_URL_FILE=$PANEL_DIR/credentials/database-url HP_VHOST_ROOT=$VHOST_ROOT HP_BACKUP_DIR=$BACKUP_DIR $PANEL_DIR/venv/bin/python $PANEL_DIR/app/hostpanel-cpanel-maint --fast >> /var/log/hostpanel-cpanel.log 2>&1
20 2 * * * root HP_DB=$PANEL_DIR/hostpanel.db HP_DATABASE_URL_FILE=$PANEL_DIR/credentials/database-url HP_VHOST_ROOT=$VHOST_ROOT HP_BACKUP_DIR=$BACKUP_DIR $PANEL_DIR/venv/bin/python $PANEL_DIR/app/hostpanel-cpanel-maint --daily >> /var/log/hostpanel-cpanel.log 2>&1
EOF
  chmod 644 /etc/cron.d/hostpanel-cpanel
  touch /var/log/hostpanel-cpanel.log
else
  rm -f /etc/cron.d/hostpanel-cpanel
  ok "controller polling omitted on a node without the control role"
fi
chmod +x "$PANEL_DIR/app/hostpanel-production-maint" "$PANEL_DIR/app/hostpanel-subaccount-sync" "$PANEL_DIR/app/hostpanel-controlplane-migrate" \
         "$PANEL_DIR/app/hostpanel-repair" "$PANEL_DIR/app/hostpanel-uninstall" "$PANEL_DIR/app/hostpanel-acceptance" \
         "$PANEL_DIR/app/hostpanel-disaster-recovery" "$PANEL_DIR/app/hostpanel-os-upgrade" \
         "$PANEL_DIR/app/hostpanel-plugin-manager" "$PANEL_DIR/app/hostpanel-cli"
ln -sf "$PANEL_DIR/app/hostpanel-cli" /usr/local/bin/hostpanel
cat >/etc/cron.d/hostpanel-production <<EOF
SHELL=/bin/bash
* * * * * root HP_DB=$PANEL_DIR/hostpanel.db HP_DATABASE_URL_FILE=$PANEL_DIR/credentials/database-url HP_MASTER_KEY_FILE=$PANEL_DIR/credentials/master.key HP_VHOST_ROOT=$VHOST_ROOT HP_BACKUP_DIR=$BACKUP_DIR $PANEL_DIR/venv/bin/python $PANEL_DIR/app/hostpanel-production-maint --jobs >> /var/log/hostpanel-production.log 2>&1
EOF
if has_role control; then
  cat >>/etc/cron.d/hostpanel-production <<EOF
7 * * * * root HP_DB=$PANEL_DIR/hostpanel.db HP_DATABASE_URL_FILE=$PANEL_DIR/credentials/database-url HP_MASTER_KEY_FILE=$PANEL_DIR/credentials/master.key HP_VHOST_ROOT=$VHOST_ROOT HP_BACKUP_DIR=$BACKUP_DIR $PANEL_DIR/venv/bin/python $PANEL_DIR/app/hostpanel-production-maint --traffic >> /var/log/hostpanel-production.log 2>&1
15 4 * * * root HP_DB=$PANEL_DIR/hostpanel.db HP_DATABASE_URL_FILE=$PANEL_DIR/credentials/database-url HP_MASTER_KEY_FILE=$PANEL_DIR/credentials/master.key HP_VHOST_ROOT=$VHOST_ROOT HP_BACKUP_DIR=$BACKUP_DIR $PANEL_DIR/venv/bin/python $PANEL_DIR/app/hostpanel-production-maint --renewals >> /var/log/hostpanel-production.log 2>&1
EOF
fi
chmod 644 /etc/cron.d/hostpanel-production
touch /var/log/hostpanel-production.log /var/log/hostpanel-proxy-traffic.log
if has_role mail; then
  cat >/etc/cron.d/hostpanel-mail-retention <<EOF
SHELL=/bin/bash
35 2 * * * root HP_MAIL_POLICY_ROOT=/etc/hostpanel/mail-convenience $PANEL_DIR/venv/bin/python $PANEL_DIR/app/hostpanel-mail-retention >> /var/log/hostpanel-mail-retention.log 2>&1
EOF
  chmod 644 /etc/cron.d/hostpanel-mail-retention
  touch /var/log/hostpanel-mail-retention.log
else
  rm -f /etc/cron.d/hostpanel-mail-retention
fi
chown "$PANEL_USER:$PANEL_USER" /var/log/hostpanel-proxy-traffic.log
chmod 640 /var/log/hostpanel-proxy-traffic.log
mkdir -p /var/lib/hostpanel /var/lib/hostpanel/migrations /var/lib/hostpanel/root-work /var/lib/hostpanel/wp-smart \
         /var/lib/hostpanel/wp-sso /var/lib/hostpanel/disaster-restore /var/lib/hostpanel/expansion \
         /var/lib/hostpanel/expansion-agent /var/lib/hostpanel/app-packages \
         "$BACKUP_DIR/disaster" /var/backups/hostpanel/migrations /var/backups/hostpanel/mariadb-seeds /opt/hostpanel/plugins
chown "$PANEL_USER:$PANEL_USER" /var/lib/hostpanel
chown "$PANEL_USER:$PANEL_USER" /var/lib/hostpanel/migrations
chown root:root /var/lib/hostpanel/root-work
chown root:root /var/lib/hostpanel/wp-smart /var/lib/hostpanel/wp-sso \
         /var/lib/hostpanel/disaster-restore /var/lib/hostpanel/expansion /var/lib/hostpanel/expansion-agent \
         /var/backups/hostpanel/migrations /var/backups/hostpanel/mariadb-seeds /opt/hostpanel/plugins
chmod 700 /var/lib/hostpanel/migrations /var/lib/hostpanel/root-work
chmod 750 /var/lib/hostpanel/wp-smart /var/lib/hostpanel/disaster-restore \
         /var/lib/hostpanel/expansion /var/lib/hostpanel/expansion-agent /opt/hostpanel/plugins
chmod 711 /var/lib/hostpanel/wp-sso
chmod 700 /var/backups/hostpanel/migrations /var/backups/hostpanel/mariadb-seeds
# Package uploads are written by the unprivileged API. The root worker never
# executes them and re-verifies checksum, paths, member types and size before use.
chown "$PANEL_USER:$PANEL_USER" /var/lib/hostpanel/app-packages
chmod 750 /var/lib/hostpanel/app-packages
has_role backup && ok "nightly backup scheduled at 03:00"

if has_role database; then
say "Securing or preserving MariaDB administration"
if [[ "$REINSTALL" == yes && -s /root/.my.cnf ]]; then
  mysql --defaults-extra-file=/root/.my.cnf -NBe 'SELECT 1' \
    >>"$LOG" 2>&1 \
    || die "The existing MariaDB root credential in /root/.my.cnf is invalid; repair it before reinstalling"
  ok "existing MariaDB root credential preserved"
else
  DB_ROOT_PASS="$(random_alnum 24)"
  mysql <<SQL >>"$LOG" 2>&1 || die "Could not secure the MariaDB root account"
ALTER USER 'root'@'localhost' IDENTIFIED BY '$DB_ROOT_PASS';
DELETE FROM mysql.user WHERE User='';
DROP DATABASE IF EXISTS test;
FLUSH PRIVILEGES;
SQL
  cat >/root/.my.cnf <<EOF
[client]
user=root
password=$DB_ROOT_PASS
EOF
  chmod 600 /root/.my.cnf
  ok "database secured"
fi

fi

# --------------------------------------------------------------------------- #
say "Running the post-install health check"
HP_DB="$PANEL_DIR/hostpanel.db" HP_BACKUP_DIR="$BACKUP_DIR" \
  "$PANEL_DIR/venv/bin/python" "$PANEL_DIR/app/hostpanel-doctor" --quiet || true

IP="$(hostname -I | awk '{print $1}')"
DATABASE_NOTE="Database role is not installed on this node."
MAIL_NOTE="Mail role is not installed on this node."
WEBMAIL_NOTE="Webmail is not installed on this node."
ISOLATION_NOTE="Web role is not installed on this node."
SPAM_NOTE="Spam filtering is not installed on this node."
APP_NOTE="Web application tools are not installed on this node."
has_role database && DATABASE_NOTE="The MariaDB root password is in /root/.my.cnf"
has_role mail && MAIL_NOTE="Generate a DKIM key under Email and publish the TXT record before production delivery."
has_role mail && SPAM_NOTE="Spam filtering is active; tune thresholds under Spam filter."
if has_role web && has_role mail; then WEBMAIL_NOTE="Webmail is at http://webmail.<yourdomain> once DNS points here."; fi
has_role web && ISOLATION_NOTE="Run Isolate all accounts under Administration after creating accounts."
has_role web && APP_NOTE="Node and Python applications, site tools and API tokens are under Web and Account."
if [[ "$ADMIN_CREATED" == yes ]]; then
  ADMIN_BANNER="   Username    :  admin
   Password    :  $ADMIN_PASS"
  ADMIN_NOTE="Save that password now — only its hash is stored."
else
  ADMIN_BANNER="   Administrator accounts and passwords were preserved."
  ADMIN_NOTE="No administrator password was changed during this run."
fi
if [[ "$REINSTALL" == yes ]]; then
  COMPLETION_TITLE="HostPanel reinstall completed"
else
  COMPLETION_TITLE="HostPanel is installed"
fi
for backup in "${TREE_ROLLBACK_BACKUPS[@]}"; do
  [[ -z "$backup" ]] || rm -rf "$backup"
done
[[ -z "$RUNTIME_OLD_DIR" ]] || rm -rf "$RUNTIME_OLD_DIR"
TREE_ROLLBACK_DESTS=()
TREE_ROLLBACK_BACKUPS=()
RUNTIME_REPLACED=no
INSTALL_COMPLETED=yes
state_write complete "completed"
cat <<BANNER

  ┌────────────────────────────────────────────────┐
  │  $COMPLETION_TITLE
  └────────────────────────────────────────────────┘

   Panel URL   :  https://$IP:$PANEL_PORT
$ADMIN_BANNER

   Panel access: $PANEL_ACCESS
   $ADMIN_NOTE
   The certificate is self-signed, so the first visit will warn.
   Installed roles: $ROLE_CSV
   Reinstall snapshot: ${REINSTALL_SNAPSHOT:-not applicable}
   $DATABASE_NOTE

   Before putting this on the public internet:
     1. If the panel port is open to everyone, restrict it:
$RESTRICT_HINT
     2. Point a hostname at this server and give the panel
        its own certificate with certbot.

   You are signed in as an administrator. Create hosting plans
   and customer accounts under Administration.

   $MAIL_NOTE
   $WEBMAIL_NOTE

   Disk quotas: $QUOTA_STATE
   Customer isolation: $ISOLATION_NOTE
   Bandwidth and visitor statistics are collected hourly.
   $SPAM_NOTE
   $APP_NOTE

   Check the installation any time with:
     $PANEL_DIR/venv/bin/python $PANEL_DIR/app/hostpanel-doctor

   Install log: $LOG

BANNER
