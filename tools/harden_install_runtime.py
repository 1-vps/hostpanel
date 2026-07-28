#!/usr/bin/env python3
"""Derive the hardened installer from the preserved, reviewed base installer.

The source file is content-addressed in the repository as install.base.sh. Every
replacement is fail-closed: a changed or ambiguous base causes the transform to
abort instead of producing a partially hardened root installer.
"""
from __future__ import annotations

import pathlib
import re
import sys


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def regex_once(text: str, pattern: str, replacement: str, label: str) -> str:
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return updated


def harden(text: str) -> str:
    text = replace_once(
        text,
        'BACKUP_DIR="/var/backups/hostpanel"\nVMAIL_UID="5000"',
        'BACKUP_DIR="/var/backups/hostpanel"\nINSTALL_SNAPSHOT_DIR="/var/backups/hostpanel-install"\nVMAIL_UID="5000"',
        "root-only snapshot directory",
    )
    text = replace_once(
        text,
        'MULTI_PHP_REPO_MODE="${HP_MULTI_PHP_REPO:-auto}"\nRSPAMD_REPO_MODE="${HP_RSPAMD_REPO:-auto}"',
        'MULTI_PHP_REPO_MODE="${HP_MULTI_PHP_REPO:-off}"\nRSPAMD_REPO_MODE="${HP_RSPAMD_REPO:-off}"',
        "external repositories default off",
    )
    text = replace_once(
        text,
        'TREE_ROLLBACK_DESTS=()\nTREE_ROLLBACK_BACKUPS=()',
        'TREE_ROLLBACK_DESTS=()\nTREE_ROLLBACK_BACKUPS=()\nNEW_PACKAGES=()\nROLLBACK_ABSENT_PATHS=""\nFIREWALL_ROLLBACK_UNIT=""\nFIREWALL_ROLLBACK_SCRIPT=""',
        "rollback state",
    )

    snapshot_block = r'''package_is_installed(){
  local package="$1"
  if [[ "$PKG_FAMILY" == debian ]]; then
    dpkg-query -W -f='${db:Status-Abbrev}' "$package" 2>/dev/null | grep -q '^ii '
  else
    rpm -q "$package" >/dev/null 2>&1
  fi
}

remember_new_package(){
  local package="$1" existing
  for existing in "${NEW_PACKAGES[@]}"; do
    [[ "$existing" == "$package" ]] && return 0
  done
  NEW_PACKAGES+=("$package")
}

rollback_new_packages(){
  ((${#NEW_PACKAGES[@]})) || return 0
  warn "Removing packages installed by the failed run: ${NEW_PACKAGES[*]}"
  if [[ "$PKG_FAMILY" == debian ]]; then
    apt-get -o DPkg::Lock::Timeout=120 remove -y -qq "${NEW_PACKAGES[@]}" >>"$LOG" 2>&1 || true
    dpkg --configure -a >>"$LOG" 2>&1 || true
  else
    dnf -y -q remove "${NEW_PACKAGES[@]}" >>"$LOG" 2>&1 || true
  fi
}

remove_paths_absent_before_install(){
  [[ -n "$ROLLBACK_ABSENT_PATHS" && -r "$ROLLBACK_ABSENT_PATHS" ]] || return 0
  local path
  while IFS= read -r path; do
    case "$path" in
      /etc/*|/opt/hostpanel/*|/usr/local/lsws/*|/root/.my.cnf) rm -rf -- "$path" ;;
      *) warn "Ignoring unsafe rollback path: $path" ;;
    esac
  done <"$ROLLBACK_ABSENT_PATHS"
}

cancel_firewall_rollback(){
  if [[ -n "$FIREWALL_ROLLBACK_UNIT" ]] && command -v systemctl >/dev/null 2>&1; then
    systemctl stop "$FIREWALL_ROLLBACK_UNIT.timer" "$FIREWALL_ROLLBACK_UNIT.service" >/dev/null 2>&1 || true
    systemctl reset-failed "$FIREWALL_ROLLBACK_UNIT.timer" "$FIREWALL_ROLLBACK_UNIT.service" >/dev/null 2>&1 || true
  fi
  [[ -z "$FIREWALL_ROLLBACK_SCRIPT" ]] || rm -f -- "$FIREWALL_ROLLBACK_SCRIPT"
  FIREWALL_ROLLBACK_UNIT=""
  FIREWALL_ROLLBACK_SCRIPT=""
}

create_reinstall_snapshot(){
  local stamp item path
  local -a items=() managed_paths=(
    /opt/hostpanel/app /opt/hostpanel/ops /opt/hostpanel/sdk /opt/hostpanel/tools
    /opt/hostpanel/integrations /opt/hostpanel/releases /opt/hostpanel/VERSION
    /opt/hostpanel/requirements.lock /opt/hostpanel/config.env
    /opt/hostpanel/credentials /opt/hostpanel/hostpanel.db /opt/hostpanel/tls
    /etc/hostpanel /etc/systemd/system/hostpanel.service /etc/sudoers.d/hostpanel
    /etc/postfix /etc/exim4 /etc/exim /etc/dovecot /etc/rspamd
    /etc/redis /etc/redis.conf /etc/apache2 /etc/httpd /etc/fstab
    /etc/ufw /etc/default/ufw /etc/firewalld /etc/yum.repos.d
    /etc/apt/sources.list.d /usr/share/keyrings/rspamd.gpg
    /usr/local/lsws/conf/httpd_config.conf /root/.my.cnf
  )
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  install -d -o root -g root -m 700 "$INSTALL_SNAPSHOT_DIR"
  REINSTALL_SNAPSHOT="$(mktemp "$INSTALL_SNAPSHOT_DIR/reinstall-${stamp}.XXXXXX.tar.gz")" \
    || die "Could not allocate a private installation safety snapshot"
  [[ -f "$REINSTALL_SNAPSHOT" && ! -L "$REINSTALL_SNAPSHOT" ]] \
    || die "Unsafe installation snapshot path"
  chown root:root "$REINSTALL_SNAPSHOT"
  chmod 600 "$REINSTALL_SNAPSHOT"
  ROLLBACK_ABSENT_PATHS="${REINSTALL_SNAPSHOT%.tar.gz}.absent"
  : >"$ROLLBACK_ABSENT_PATHS"
  chown root:root "$ROLLBACK_ABSENT_PATHS"
  chmod 600 "$ROLLBACK_ABSENT_PATHS"
  for path in "${managed_paths[@]}"; do
    if [[ -e "$path" || -L "$path" ]]; then
      items+=("${path#/}")
    else
      printf '%s\n' "$path" >>"$ROLLBACK_ABSENT_PATHS"
    fi
  done
  while IFS= read -r item; do
    item="${item#/}"
    [[ -n "$item" ]] && items+=("$item")
  done < <(
    find /etc/cron.d /etc/nginx /usr/local/lsws/conf/vhosts \
      -maxdepth 3 -name '*hostpanel*' -print 2>/dev/null || true
  )
  if ((${#items[@]})); then
    tar -C / -czf "$REINSTALL_SNAPSHOT" --ignore-failed-read \
      "${items[@]}" >>"$LOG" 2>&1 \
      || die "Could not create the installation safety snapshot"
  else
    tar -C / -czf "$REINSTALL_SNAPSHOT" --files-from /dev/null \
      >>"$LOG" 2>&1 || die "Could not create the installation safety snapshot"
  fi
  chown root:root "$REINSTALL_SNAPSHOT"
  chmod 600 "$REINSTALL_SNAPSHOT"
  printf '%s\n' "$REINSTALL_SNAPSHOT" >/etc/hostpanel/last-reinstall-snapshot
  chown root:root /etc/hostpanel/last-reinstall-snapshot
  chmod 600 /etc/hostpanel/last-reinstall-snapshot
  state_write in-progress "installation safety snapshot created"
  ok "root-only installation safety snapshot: $REINSTALL_SNAPSHOT"
}

installer_exit(){'''
    text = regex_once(
        text,
        r'create_reinstall_snapshot\(\)\{.*?\n\}\n\ninstaller_exit\(\)\{',
        snapshot_block,
        "snapshot and rollback helpers",
    )
    text = replace_once(
        text,
        '''      if [[ "$REINSTALL" == yes && -s "$REINSTALL_SNAPSHOT" ]]; then
        tar -C / -xzf "$REINSTALL_SNAPSHOT" >>"$LOG" 2>&1 || true
        command -v systemctl >/dev/null 2>&1 && systemctl daemon-reload >>"$LOG" 2>&1 || true
      fi
      state_write failed "$CURRENT_STAGE"''',
        '''      remove_paths_absent_before_install
      if [[ -s "$REINSTALL_SNAPSHOT" ]]; then
        tar -C / -xzf "$REINSTALL_SNAPSHOT" >>"$LOG" 2>&1 || true
        command -v systemctl >/dev/null 2>&1 && systemctl daemon-reload >>"$LOG" 2>&1 || true
      fi
      rollback_new_packages
      if [[ "$PKG_FAMILY" == debian ]]; then
        command -v ufw >/dev/null 2>&1 && ufw reload >>"$LOG" 2>&1 || true
      else
        command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --reload >>"$LOG" 2>&1 || true
      fi
      state_write failed "$CURRENT_STAGE"''',
        "restore failed installation",
    )
    text = replace_once(
        text,
        '''if [[ "$REINSTALL" == yes ]]; then
  say "Preparing safe reinstall"
  create_reinstall_snapshot
  if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet hostpanel 2>/dev/null; then''',
        '''say "Creating an installation safety snapshot"
create_reinstall_snapshot

if [[ "$REINSTALL" == yes ]]; then
  say "Preparing safe reinstall"
  if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet hostpanel 2>/dev/null; then''',
        "snapshot every mutating run",
    )
    text = text.replace('MTA_SWITCH_BACKUP="$BACKUP_DIR/install/', 'MTA_SWITCH_BACKUP="$INSTALL_SNAPSHOT_DIR/')
    text = text.replace('mkdir -p "$BACKUP_DIR/install"', 'install -d -o root -g root -m 700 "$INSTALL_SNAPSHOT_DIR"')
    text = text.replace('"$BACKUP_DIR/install/firewall-before.txt"', '"$INSTALL_SNAPSHOT_DIR/firewall-before.txt"')

    text = regex_once(
        text,
        r'''pkg_install\(\)\{\n  \(\(\$#\)\) \|\| return 0\n  local packages=\("\$@"\)\n  pkg_try_install "\$\{packages\[@\]\}" && return 0\n  pkg_failure "installing: \$\{packages\[\*\]\}"\n\}''',
        '''pkg_install(){
  (($#)) || return 0
  local package
  local packages=("$@") new_candidates=()
  for package in "${packages[@]}"; do
    [[ "$package" == *://* ]] && continue
    package_is_installed "$package" || new_candidates+=("$package")
  done
  if pkg_try_install "${packages[@]}"; then
    for package in "${new_candidates[@]}"; do
      package_is_installed "$package" && remember_new_package "$package"
    done
    return 0
  fi
  for package in "${new_candidates[@]}"; do
    package_is_installed "$package" && remember_new_package "$package"
  done
  pkg_failure "installing: ${packages[*]}"
}''',
        "package transaction tracking",
    )

    text = regex_once(
        text,
        r'# ---- Enterprise Linux repositories.*?\npkg_refresh\n\nCOMMON_PACKAGES=',
        '''# ---- External repositories ---------------------------------------------
# Root installers must not execute mutable vendor scripts or import changing
# repository bootstrap packages. Operators may preconfigure a reviewed external
# repository before installation; automatic repository mutation is disabled.
if [[ "$MULTI_PHP_REPO_MODE" != off || "$RSPAMD_REPO_MODE" != off ]]; then
  die "Automatic external repository setup is disabled. Preconfigure a reviewed repository, then rerun with HP_MULTI_PHP_REPO=off and HP_RSPAMD_REPO=off."
fi
pkg_refresh

COMMON_PACKAGES=''',
        "remove mutable external repository bootstrap",
    )

    text = replace_once(
        text,
        'PHP_BRANCHES=(8.5 8.4 8.3 8.2)',
        'PHP_BRANCHES=(8.5 8.4 8.3 8.2 8.1)',
        "native Ubuntu 22.04 PHP branch",
    )
    text = replace_once(
        text,
        '''if has_role web; then
say "Installing OpenLiteSpeed and LSPHP"''',
        '''if has_role web && pkg_available "$(pkg_name openlitespeed)"; then
say "Installing OpenLiteSpeed and LSPHP"''',
        "optional OpenLiteSpeed",
    )
    text = replace_once(
        text,
        '''fi

# --------------------------------------------------------------------------- #
say "Configuring the firewall"''',
        '''elif has_role web; then
  warn "OpenLiteSpeed is unavailable from reviewed repositories; continuing with nginx and Apache"
  rm -f /etc/hostpanel/lsphp-versions /etc/hostpanel/lsphp-skipped-packages
fi

# --------------------------------------------------------------------------- #
say "Configuring the firewall"''',
        "OpenLiteSpeed safe fallback",
    )

    firewall_helpers = r'''fw_commit(){
  if [[ "$FW_TOOL" == firewalld ]]; then
    firewall-cmd --reload >>"$LOG" 2>&1 || die "firewalld reload failed"
  else
    ufw --force enable >>"$LOG" 2>&1 || die "ufw could not be enabled"
  fi
}

schedule_firewall_rollback(){
  command -v systemd-run >/dev/null 2>&1 \
    || die "systemd-run is required for the timed firewall safety rollback"
  install -d -o root -g root -m 700 "$INSTALL_SNAPSHOT_DIR"
  FIREWALL_ROLLBACK_UNIT="hostpanel-firewall-rollback-$$"
  FIREWALL_ROLLBACK_SCRIPT="$INSTALL_SNAPSHOT_DIR/${FIREWALL_ROLLBACK_UNIT}.sh"
  local was_active=no
  fw_is_active && was_active=yes
  cat >"$FIREWALL_ROLLBACK_SCRIPT" <<EOF
#!/usr/bin/env bash
set -u
tar -C / -xzf "$REINSTALL_SNAPSHOT" etc/ufw etc/default/ufw etc/firewalld 2>/dev/null || true
if [[ "$FW_TOOL" == firewalld ]]; then
  if [[ "$was_active" == yes ]]; then firewall-cmd --reload || true; else systemctl disable --now firewalld || true; fi
else
  if [[ "$was_active" == yes ]]; then ufw reload || true; else ufw --force disable || true; fi
fi
rm -f -- "$FIREWALL_ROLLBACK_SCRIPT"
EOF
  chown root:root "$FIREWALL_ROLLBACK_SCRIPT"
  chmod 700 "$FIREWALL_ROLLBACK_SCRIPT"
  systemd-run --quiet --unit="$FIREWALL_ROLLBACK_UNIT" --on-active=5m \
    /bin/bash "$FIREWALL_ROLLBACK_SCRIPT" >>"$LOG" 2>&1 \
    || die "Could not schedule automatic firewall rollback"
}
'''
    text = regex_once(
        text,
        r'fw_commit\(\)\{.*?\n\}\n\n# ---- Enterprise Linux repositories',
        firewall_helpers + '\n# ---- Enterprise Linux repositories',
        "timed firewall rollback helper",
    )
    text = replace_once(
        text,
        '''FW_WAS_ACTIVE=no
fw_is_active && FW_WAS_ACTIVE=yes
fw_prepare
if [[ "$FW_WAS_ACTIVE" == no ]]; then''',
        '''FW_WAS_ACTIVE=no
fw_is_active && FW_WAS_ACTIVE=yes
schedule_firewall_rollback
fw_prepare
if [[ "$FW_WAS_ACTIVE" == no ]]; then''',
        "schedule firewall rollback",
    )
    text = replace_once(
        text,
        'FIREWALL_PORTS=(22/tcp)',
        '''SSH_PORTS=()
if [[ -n "${SSH_CONNECTION:-}" ]]; then
  ssh_connection_port="$(awk '{print $4}' <<<"$SSH_CONNECTION")"
  [[ "$ssh_connection_port" =~ ^[0-9]+$ ]] && SSH_PORTS+=("$ssh_connection_port")
fi
if command -v sshd >/dev/null 2>&1; then
  while IFS= read -r ssh_port; do
    [[ "$ssh_port" =~ ^[0-9]+$ ]] && SSH_PORTS+=("$ssh_port")
  done < <(sshd -T 2>/dev/null | awk '$1=="port" {print $2}' | sort -un)
fi
((${#SSH_PORTS[@]})) || SSH_PORTS=(22)
FIREWALL_PORTS=()
for ssh_port in "${SSH_PORTS[@]}"; do FIREWALL_PORTS+=("${ssh_port}/tcp"); done''',
        "preserve actual SSH ports",
    )
    text = regex_once(
        text,
        r'''ADMIN_IP="\$\{SSH_CLIENT:-\}"\nADMIN_IP="\$\{ADMIN_IP%% \*\}"\nif \[\[ -n "\$ADMIN_IP" \]\]; then\n  ADMIN_IP="\$\(python3 - "\$ADMIN_IP" <<'PYIP'.*?PYIP\n\)" \|\| die "Invalid SSH_CLIENT source address"\n.*?  PANEL_ACCESS="restricted to \$ADMIN_IP"\nelse''',
        '''ADMIN_IP="${HP_PANEL_ADMIN_CIDR:-}"
if [[ -z "$ADMIN_IP" ]]; then
  ADMIN_IP="${SSH_CLIENT:-}"
  ADMIN_IP="${ADMIN_IP%% *}"
fi
if [[ -n "$ADMIN_IP" ]]; then
  ADMIN_IP="$(python3 - "$ADMIN_IP" <<'PYIP'
import ipaddress
import sys

try:
    raw = sys.argv[1]
    value = ipaddress.ip_network(raw, strict=False) if "/" in raw else ipaddress.ip_address(raw)
    address = value.network_address if hasattr(value, "network_address") else value
    if getattr(address, "scope_id", None):
        raise ValueError("scoped IPv6 is unsupported")
    print(value)
except ValueError as exc:
    raise SystemExit(f"invalid administrative source: {exc}")
PYIP
)" || die "HP_PANEL_ADMIN_CIDR or SSH_CLIENT contains an invalid address"
  [[ "$FW_TOOL" == ufw ]] && ufw --force delete allow "${PANEL_PORT}/tcp" >>"$LOG" 2>&1 || true
  fw_allow_from "$ADMIN_IP" "$PANEL_PORT"
  PANEL_ACCESS="restricted to $ADMIN_IP"
else''',
        "administrative CIDR",
    )
    text = replace_once(
        text,
        '''else
  fw_allow "${PANEL_PORT}/tcp"
  PANEL_ACCESS="OPEN TO THE INTERNET — restrict it, see below"
fi''',
        '''else
  [[ "${HP_ALLOW_PUBLIC_PANEL:-no}" == yes ]] \
    || die "No administrative source address was detected. Set HP_PANEL_ADMIN_CIDR, install over SSH, or explicitly set HP_ALLOW_PUBLIC_PANEL=yes."
  fw_allow "${PANEL_PORT}/tcp"
  PANEL_ACCESS="OPEN TO THE INTERNET — explicitly requested"
fi''',
        "fail closed on public panel exposure",
    )
    text = replace_once(
        text,
        '''fw_allow 21/tcp
fw_allow 40000:40100/tcp
systemctl restart vsftpd''',
        '''fw_allow 21/tcp
fw_allow 40000:40100/tcp
fw_commit
systemctl restart vsftpd''',
        "activate FTP firewalld rules",
    )

    text = replace_once(
        text,
        '''ADMIN_RESULT="$(HP_DATABASE_URL_FILE=/nonexistent HP_DB="$PANEL_DIR/hostpanel.db" "$PANEL_DIR/venv/bin/python" -c "
import sys; sys.path.insert(0, '$PANEL_DIR/app')
import store
created = store.init('admin', '$ADMIN_PASS')
print(created or '')
" 2>>"$LOG")" || die "Could not initialise the HostPanel recovery database"''',
        '''ADMIN_RESULT="$(printf '%s' "$ADMIN_PASS" | HP_DATABASE_URL_FILE=/nonexistent HP_DB="$PANEL_DIR/hostpanel.db" "$PANEL_DIR/venv/bin/python" -c "
import sys; sys.path.insert(0, '$PANEL_DIR/app')
import store
password = sys.stdin.read()
created = store.init('admin', password)
print(created or '')
" 2>>"$LOG")" || die "Could not initialise the HostPanel recovery database"''',
        "administrator password stdin handoff",
    )

    text = replace_once(
        text,
        '''mkdir -p "$PANEL_DIR/credentials"
chmod 700 "$PANEL_DIR/credentials"''',
        '''mkdir -p "$PANEL_DIR/credentials"
chmod 700 "$PANEL_DIR/credentials"
REDIS_PASSWORD_FILE="$PANEL_DIR/credentials/redis-password"
if has_role web || has_role database || has_role mail; then
  [[ -s "$REDIS_PASSWORD_FILE" ]] || random_alnum 48 >"$REDIS_PASSWORD_FILE"
  chmod 600 "$REDIS_PASSWORD_FILE"
  REDIS_PASSWORD="$(head -1 "$REDIS_PASSWORD_FILE")"
else
  REDIS_PASSWORD=""
fi''',
        "Redis credential creation",
    )
    text = replace_once(
        text,
        'HP_MAIL_MTA=$MAIL_MTA\nHP_CONTROL_MEMBER=$CONTROL_MEMBER',
        'HP_MAIL_MTA=$MAIL_MTA\nHP_REDIS_URL=${REDIS_PASSWORD:+redis://hostpanel:$REDIS_PASSWORD@127.0.0.1:6379/0}\nHP_CONTROL_MEMBER=$CONTROL_MEMBER',
        "Redis application URL",
    )
    text = replace_once(
        text,
        'HP_NODE_ROLES|HP_MAIL_MTA|HP_CONTROL_MEMBER',
        'HP_NODE_ROLES|HP_MAIL_MTA|HP_REDIS_URL|HP_CONTROL_MEMBER',
        "managed Redis config key",
    )
    text = regex_once(
        text,
        r'''  REDIS_ACL_DIR="\$\(dirname "\$REDIS_CONF"\)"\n  REDIS_ACL="\$REDIS_ACL_DIR/users\.acl"\n  touch "\$REDIS_ACL"\n  chown redis:redis "\$REDIS_ACL"\n  grep -q "\^aclfile" "\$REDIS_CONF" \|\| echo "aclfile \$REDIS_ACL" >>"\$REDIS_CONF"''',
        '''  REDIS_ACL_DIR="$(dirname "$REDIS_CONF")"
  REDIS_ACL="$REDIS_ACL_DIR/users.acl"
  REDIS_ACL_NEW="$REDIS_ACL.new.$$"
  cat >"$REDIS_ACL_NEW" <<EOF
user default off
user hostpanel on >$REDIS_PASSWORD ~* &* +@all
EOF
  chown redis:redis "$REDIS_ACL_NEW"
  chmod 640 "$REDIS_ACL_NEW"
  mv -f "$REDIS_ACL_NEW" "$REDIS_ACL"
  if grep -q '^[[:space:]]*aclfile[[:space:]]' "$REDIS_CONF"; then
    sed -ri "s|^[[:space:]]*aclfile[[:space:]].*|aclfile $REDIS_ACL|" "$REDIS_CONF"
  else
    echo "aclfile $REDIS_ACL" >>"$REDIS_CONF"
  fi''',
        "Redis ACL default user hardening",
    )
    text = replace_once(
        text,
        '''cat >/etc/rspamd/local.d/redis.conf <<'EOF'
servers = "127.0.0.1";
EOF''',
        '''cat >/etc/rspamd/local.d/redis.conf <<EOF
servers = "127.0.0.1";
username = "hostpanel";
password = "$REDIS_PASSWORD";
EOF''',
        "Rspamd Redis authentication",
    )

    fatal_replacements = (
        ('systemctl restart dovecot >>"$LOG" 2>&1 || true', 'systemctl restart dovecot >>"$LOG" 2>&1 || die "Dovecot failed to restart"', "Dovecot"),
        ('systemctl restart "$(svc redis)" >>"$LOG" 2>&1 || true', 'systemctl restart "$(svc redis)" >>"$LOG" 2>&1 || die "Redis failed to restart with the managed ACL"', "Redis restart"),
        ('systemctl enable --now "$(svc redis)" >>"$LOG" 2>&1 || true', 'systemctl enable --now "$(svc redis)" >>"$LOG" 2>&1 || die "Redis failed to start"', "Redis start"),
        ('systemctl enable --now postgresql >>"$LOG" 2>&1 || true', 'systemctl enable --now postgresql >>"$LOG" 2>&1 || die "PostgreSQL failed to start"', "PostgreSQL"),
        ('systemctl restart rspamd postfix >>"$LOG" 2>&1 || true', 'systemctl restart rspamd postfix >>"$LOG" 2>&1 || die "Rspamd or Postfix failed to restart"', "Postfix mail stack"),
        ('systemctl restart rspamd "$(svc exim)" >>"$LOG" 2>&1 || true', 'systemctl restart rspamd "$(svc exim)" >>"$LOG" 2>&1 || die "Rspamd or Exim failed to restart"', "Exim mail stack"),
        ('if "$APACHE_CTL" configtest >>"$LOG" 2>&1; then systemctl restart "$(svc apache)" >>"$LOG" 2>&1 || true; fi', '"$APACHE_CTL" configtest >>"$LOG" 2>&1 || die "Apache configuration is invalid"\n  systemctl restart "$(svc apache)" >>"$LOG" 2>&1 || die "Apache failed to restart"', "Apache"),
        ('"$PANEL_DIR/venv/bin/python" "$PANEL_DIR/app/hostpanel-doctor" --quiet || true', '"$PANEL_DIR/venv/bin/python" "$PANEL_DIR/app/hostpanel-doctor" --quiet || die "Post-install health check failed"', "doctor"),
    )
    for old, new, label in fatal_replacements:
        text = replace_once(text, old, new, f"fail closed: {label}")

    text = replace_once(
        text,
        '''    -subj "/CN=$(hostname -f)" \\
    -addext "subjectAltName=DNS:$(hostname -f),IP:$(hostname -I | awk '{print $1}')" \\''',
        '''    -subj "/CN=$PANEL_HOST" \\
    -addext "subjectAltName=DNS:$PANEL_HOST,IP:$(hostname -I | awk '{print $1}')" \\''',
        "configured panel certificate hostname",
    )

    text = replace_once(
        text,
        'PHP_INSTALLED=(); mkdir -p /run/php',
        'PHP_INSTALLED=(); PHP_SKIPPED_MODULES=(); mkdir -p /run/php',
        "PHP module tracking",
    )
    text = replace_once(
        text,
        '''    package="$(php_package_for "$version" "$suffix" || true)"
    [[ -n "$package" && "$seen" != *" $package "* ]] || continue
    seen+="$package "; packages+=("$package")''',
        '''    package="$(php_package_for "$version" "$suffix" || true)"
    if [[ -z "$package" ]]; then
      PHP_SKIPPED_MODULES+=("php${version}-${suffix}")
      continue
    fi
    [[ "$seen" != *" $package "* ]] || continue
    seen+="$package "; packages+=("$package")''',
        "record unavailable PHP modules",
    )
    text = replace_once(
        text,
        '''printf '%s\n' "${PHP_INSTALLED[@]}" >/etc/hostpanel/php-versions
ok "PHP-FPM installed: ${PHP_INSTALLED[*]}"''',
        '''printf '%s\n' "${PHP_INSTALLED[@]}" >/etc/hostpanel/php-versions
if ((${#PHP_SKIPPED_MODULES[@]})); then
  printf '%s\n' "${PHP_SKIPPED_MODULES[@]}" | sort -u >/etc/hostpanel/php-skipped-packages
  warn "Some optional PHP modules were unavailable; recorded in /etc/hostpanel/php-skipped-packages"
else
  rm -f /etc/hostpanel/php-skipped-packages
fi
for version in "${PHP_INSTALLED[@]}"; do
  php_cli="$(php_cli_bin "$version")"
  php_modules="$($php_cli -m 2>>"$LOG")" || die "PHP $version could not enumerate loaded modules"
  for requirement in curl mbstring SimpleXML zip gd intl bcmath soap pgsql sqlite3; do
    grep -Eqi "^${requirement}$" <<<"$php_modules" \
      || die "PHP $version is missing required loaded module: $requirement"
  done
  grep -Eqi '^(mysqli|pdo_mysql)$' <<<"$php_modules" \
    || die "PHP $version is missing a MySQL driver"
  grep -Eqi '^(Zend OPcache|opcache)$' <<<"$php_modules" \
    || die "PHP $version is missing OPcache"
done
ok "PHP-FPM installed: ${PHP_INSTALLED[*]}"''',
        "validate loaded PHP baseline",
    )

    text = replace_once(
        text,
        '''for backup in "${TREE_ROLLBACK_BACKUPS[@]}"; do
  [[ -z "$backup" ]] || rm -rf "$backup"
done''',
        '''cancel_firewall_rollback
for backup in "${TREE_ROLLBACK_BACKUPS[@]}"; do
  [[ -z "$backup" ]] || rm -rf "$backup"
done''',
        "cancel timed firewall rollback after success",
    )

    marker = "# Hardened deterministically by tools/harden_install_runtime.py\n"
    return text.replace("#!/usr/bin/env bash\n", "#!/usr/bin/env bash\n" + marker, 1)


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: harden_install_runtime.py SOURCE DESTINATION")
    source = pathlib.Path(sys.argv[1])
    destination = pathlib.Path(sys.argv[2])
    if not source.is_file() or source.is_symlink():
        raise SystemExit(f"unsafe installer base: {source}")
    transformed = harden(source.read_text(encoding="utf-8"))
    destination.write_text(transformed, encoding="utf-8")
    destination.chmod(0o700)


if __name__ == "__main__":
    main()
