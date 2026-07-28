#!/usr/bin/env python3
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    (ROOT / path).parent.mkdir(parents=True, exist_ok=True)
    (ROOT / path).write_text(text, encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def regex_once(text: str, pattern: str, replacement: str, label: str) -> str:
    text, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
    if count != 1:
        raise SystemExit(f"{label}: expected one regex match, found {count}")
    return text


installer = read("install.sh")
installer = replace_once(
    installer,
    'BACKUP_DIR="/var/backups/hostpanel"\nVMAIL_UID="5000"',
    'BACKUP_DIR="/var/backups/hostpanel"\nINSTALL_SNAPSHOT_DIR="/var/backups/hostpanel-install"\nVMAIL_UID="5000"',
    "snapshot directory variable",
)
installer = replace_once(
    installer,
    'MULTI_PHP_REPO_MODE="${HP_MULTI_PHP_REPO:-auto}"\nRSPAMD_REPO_MODE="${HP_RSPAMD_REPO:-auto}"',
    'MULTI_PHP_REPO_MODE="${HP_MULTI_PHP_REPO:-off}"\nRSPAMD_REPO_MODE="${HP_RSPAMD_REPO:-off}"',
    "safe external repository defaults",
)
installer = replace_once(
    installer,
    'TREE_ROLLBACK_DESTS=()\nTREE_ROLLBACK_BACKUPS=()',
    'TREE_ROLLBACK_DESTS=()\nTREE_ROLLBACK_BACKUPS=()\nNEW_PACKAGES=()\nFIREWALL_ROLLBACK_UNIT=""\nFIREWALL_ROLLBACK_SCRIPT=""',
    "rollback state",
)

snapshot_functions = r'''package_is_installed(){
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
  local stamp item
  local -a items=()
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  install -d -o root -g root -m 700 "$INSTALL_SNAPSHOT_DIR"
  REINSTALL_SNAPSHOT="$(mktemp "$INSTALL_SNAPSHOT_DIR/reinstall-${stamp}.XXXXXX.tar.gz")" \
    || die "Could not allocate a private reinstall safety snapshot"
  [[ -f "$REINSTALL_SNAPSHOT" && ! -L "$REINSTALL_SNAPSHOT" ]] \
    || die "Unsafe reinstall snapshot path"
  chmod 600 "$REINSTALL_SNAPSHOT"
  for item in \
    opt/hostpanel/app opt/hostpanel/ops opt/hostpanel/sdk opt/hostpanel/tools \
    opt/hostpanel/integrations opt/hostpanel/releases opt/hostpanel/VERSION \
    opt/hostpanel/requirements.lock opt/hostpanel/config.env \
    opt/hostpanel/credentials opt/hostpanel/hostpanel.db opt/hostpanel/tls \
    etc/hostpanel etc/systemd/system/hostpanel.service etc/sudoers.d/hostpanel \
    etc/apt/sources.list.d etc/yum.repos.d usr/share/keyrings/rspamd.gpg \
    etc/postfix etc/exim4 etc/exim etc/dovecot etc/rspamd etc/redis etc/redis.conf \
    etc/apache2 etc/httpd etc/firewalld etc/ufw etc/fstab \
    usr/local/lsws/conf/httpd_config.conf root/.my.cnf; do
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
  chown root:root "$REINSTALL_SNAPSHOT"
  chmod 600 "$REINSTALL_SNAPSHOT"
  printf '%s\n' "$REINSTALL_SNAPSHOT" >/etc/hostpanel/last-reinstall-snapshot
  chown root:root /etc/hostpanel/last-reinstall-snapshot
  chmod 600 /etc/hostpanel/last-reinstall-snapshot
  state_write in-progress "installation safety snapshot created"
  ok "root-only installation safety snapshot: $REINSTALL_SNAPSHOT"
}

installer_exit(){'''
installer = regex_once(
    installer,
    r'create_reinstall_snapshot\(\)\{.*?\n\}\n\ninstaller_exit\(\)\{',
    snapshot_functions,
    "snapshot and rollback functions",
)
installer = replace_once(
    installer,
    '''      if [[ "$REINSTALL" == yes && -s "$REINSTALL_SNAPSHOT" ]]; then
        tar -C / -xzf "$REINSTALL_SNAPSHOT" >>"$LOG" 2>&1 || true
        command -v systemctl >/dev/null 2>&1 && systemctl daemon-reload >>"$LOG" 2>&1 || true
      fi
      state_write failed "$CURRENT_STAGE"''',
    '''      if [[ -s "$REINSTALL_SNAPSHOT" ]]; then
        tar -C / -xzf "$REINSTALL_SNAPSHOT" >>"$LOG" 2>&1 || true
        command -v systemctl >/dev/null 2>&1 && systemctl daemon-reload >>"$LOG" 2>&1 || true
        if [[ "$PKG_FAMILY" == debian ]]; then
          ufw reload >>"$LOG" 2>&1 || true
        else
          firewall-cmd --reload >>"$LOG" 2>&1 || true
        fi
      fi
      rollback_new_packages
      state_write failed "$CURRENT_STAGE"''',
    "failure rollback",
)
installer = replace_once(
    installer,
    '''if [[ "$REINSTALL" == yes ]]; then
  say "Preparing safe reinstall"
  create_reinstall_snapshot
  if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet hostpanel 2>/dev/null; then''',
    '''say "Creating an installation safety snapshot"
create_reinstall_snapshot

if [[ "$REINSTALL" == yes ]]; then
  say "Preparing safe reinstall"
  if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet hostpanel 2>/dev/null; then''',
    "snapshot every mutating install",
)
installer = installer.replace('MTA_SWITCH_BACKUP="$BACKUP_DIR/install/', 'MTA_SWITCH_BACKUP="$INSTALL_SNAPSHOT_DIR/')
installer = installer.replace('mkdir -p "$BACKUP_DIR/install"', 'install -d -o root -g root -m 700 "$INSTALL_SNAPSHOT_DIR"')
installer = installer.replace('"$BACKUP_DIR/install/firewall-before.txt"', '"$INSTALL_SNAPSHOT_DIR/firewall-before.txt"')

installer = regex_once(
    installer,
    r'''pkg_install\(\)\{\n  \(\(\$#\)\) \|\| return 0\n  local packages=\("\$@"\)\n  pkg_try_install "\$\{packages\[@\]\}" && return 0\n  pkg_failure "installing: \$\{packages\[\*\]\}"\n\}''',
    '''pkg_install(){
  (($#)) || return 0
  local package
  local packages=("$@") new_candidates=()
  for package in "${packages[@]}"; do
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

installer = replace_once(
    installer,
    '''  LITESPEED_REPO_SCRIPT="$BOOTSTRAP_TEMP_DIR/litespeed-repo.sh"
  curl -fsSL https://repo.litespeed.sh -o "$LITESPEED_REPO_SCRIPT" >>"$LOG" 2>&1 || die "Could not download the LiteSpeed repository installer"
  bash "$LITESPEED_REPO_SCRIPT" >>"$LOG" 2>&1 || die "Could not enable the LiteSpeed repository"
  rm -f "$LITESPEED_REPO_SCRIPT"
''',
    '''  # OpenLiteSpeed is optional and is installed only when a package is already
  # available from a repository the operator has independently configured.
  # Never execute a mutable vendor shell script as root.
''',
    "remove mutable LiteSpeed script execution",
)
installer = replace_once(
    installer,
    '''if has_role web; then
say "Installing OpenLiteSpeed and LSPHP"''',
    '''if has_role web && pkg_available "$(pkg_name openlitespeed)"; then
say "Installing OpenLiteSpeed and LSPHP"''',
    "OpenLiteSpeed availability guard",
)
installer = replace_once(
    installer,
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
  command -v systemd-run >/dev/null 2>&1 || {
    warn "systemd-run is unavailable; automatic firewall rollback cannot be scheduled"
    return 0
  }
  install -d -o root -g root -m 700 "$INSTALL_SNAPSHOT_DIR"
  FIREWALL_ROLLBACK_UNIT="hostpanel-firewall-rollback-$$"
  FIREWALL_ROLLBACK_SCRIPT="$INSTALL_SNAPSHOT_DIR/${FIREWALL_ROLLBACK_UNIT}.sh"
  local was_active=no
  fw_is_active && was_active=yes
  cat >"$FIREWALL_ROLLBACK_SCRIPT" <<EOF
#!/usr/bin/env bash
set -u
if [[ "$FW_TOOL" == firewalld ]]; then
  if [[ "$was_active" == yes ]]; then firewall-cmd --reload || true; else systemctl disable --now firewalld || true; fi
else
  if [[ "$was_active" == yes ]]; then ufw reload || true; else ufw --force disable || true; fi
fi
EOF
  chown root:root "$FIREWALL_ROLLBACK_SCRIPT"
  chmod 700 "$FIREWALL_ROLLBACK_SCRIPT"
  systemd-run --quiet --unit="$FIREWALL_ROLLBACK_UNIT" --on-active=5m \
    /bin/bash "$FIREWALL_ROLLBACK_SCRIPT" >>"$LOG" 2>&1 \
    || die "Could not schedule automatic firewall rollback"
}
'''
installer = regex_once(
    installer,
    r'''fw_commit\(\)\{\n.*?\n\}\n\n# ---- Enterprise Linux repositories''',
    firewall_helpers + '\n# ---- Enterprise Linux repositories',
    "timed firewall rollback helper",
)
installer = replace_once(
    installer,
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
installer = replace_once(
    installer,
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
    "preserve custom SSH ports",
)

installer = regex_once(
    installer,
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
    value = sys.argv[1]
    if "/" in value:
        network = ipaddress.ip_network(value, strict=False)
        if getattr(network.network_address, "scope_id", None):
            raise ValueError("scoped IPv6 is unsupported")
        print(network)
    else:
        address = ipaddress.ip_address(value)
        if getattr(address, "scope_id", None):
            raise ValueError("scoped IPv6 is unsupported")
        print(address)
except ValueError as exc:
    raise SystemExit(f"Invalid administrative source address: {exc}")
PYIP
)" || die "HP_PANEL_ADMIN_CIDR or SSH_CLIENT contains an invalid address"
  [[ "$FW_TOOL" == ufw ]] && ufw --force delete allow "${PANEL_PORT}/tcp" >>"$LOG" 2>&1 || true
  fw_allow_from "$ADMIN_IP" "$PANEL_PORT"
  PANEL_ACCESS="restricted to $ADMIN_IP"
else''',
    "administrative CIDR support",
)

installer = replace_once(
    installer,
    '''fw_allow 21/tcp
fw_allow 40000:40100/tcp
systemctl restart vsftpd''',
    '''fw_allow 21/tcp
fw_allow 40000:40100/tcp
fw_commit
systemctl restart vsftpd''',
    "activate FTP firewalld rules",
)

installer = replace_once(
    installer,
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

installer = replace_once(
    installer,
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
installer = replace_once(
    installer,
    'HP_MAIL_MTA=$MAIL_MTA\nHP_CONTROL_MEMBER=$CONTROL_MEMBER',
    'HP_MAIL_MTA=$MAIL_MTA\nHP_REDIS_URL=${REDIS_PASSWORD:+redis://hostpanel:$REDIS_PASSWORD@127.0.0.1:6379/0}\nHP_CONTROL_MEMBER=$CONTROL_MEMBER',
    "Redis application URL",
)
installer = replace_once(
    installer,
    'HP_NODE_ROLES|HP_MAIL_MTA|HP_CONTROL_MEMBER',
    'HP_NODE_ROLES|HP_MAIL_MTA|HP_REDIS_URL|HP_CONTROL_MEMBER',
    "managed Redis config key",
)

installer = regex_once(
    installer,
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
installer = installer.replace('systemctl restart "$(svc redis)" >>"$LOG" 2>&1 || true', 'systemctl restart "$(svc redis)" >>"$LOG" 2>&1 || die "Redis failed to restart with the managed ACL"')
installer = replace_once(
    installer,
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

for old, new, label in [
    ('systemctl restart dovecot >>"$LOG" 2>&1 || true', 'systemctl restart dovecot >>"$LOG" 2>&1 || die "Dovecot failed to restart"', 'Dovecot fatal restart'),
    ('systemctl enable --now "$(svc redis)" >>"$LOG" 2>&1 || true', 'systemctl enable --now "$(svc redis)" >>"$LOG" 2>&1 || die "Redis failed to start"', 'Redis fatal start'),
    ('systemctl enable --now postgresql >>"$LOG" 2>&1 || true', 'systemctl enable --now postgresql >>"$LOG" 2>&1 || die "PostgreSQL failed to start"', 'PostgreSQL fatal start'),
    ('systemctl restart rspamd postfix >>"$LOG" 2>&1 || true', 'systemctl restart rspamd postfix >>"$LOG" 2>&1 || die "Rspamd or Postfix failed to restart"', 'Rspamd/Postfix fatal restart'),
    ('systemctl restart rspamd "$(svc exim)" >>"$LOG" 2>&1 || true', 'systemctl restart rspamd "$(svc exim)" >>"$LOG" 2>&1 || die "Rspamd or Exim failed to restart"', 'Rspamd/Exim fatal restart'),
    ('if "$APACHE_CTL" configtest >>"$LOG" 2>&1; then systemctl restart "$(svc apache)" >>"$LOG" 2>&1 || true; fi', '"$APACHE_CTL" configtest >>"$LOG" 2>&1 || die "Apache configuration is invalid"\n  systemctl restart "$(svc apache)" >>"$LOG" 2>&1 || die "Apache failed to restart"', 'Apache fatal validation'),
    ('"$PANEL_DIR/venv/bin/python" "$PANEL_DIR/app/hostpanel-doctor" --quiet || true', '"$PANEL_DIR/venv/bin/python" "$PANEL_DIR/app/hostpanel-doctor" --quiet || die "Post-install health check failed"', 'doctor fatal'),
]:
    installer = replace_once(installer, old, new, label)

installer = replace_once(
    installer,
    '''    -subj "/CN=$(hostname -f)" \\
    -addext "subjectAltName=DNS:$(hostname -f),IP:$(hostname -I | awk '{print $1}')" \\''',
    '''    -subj "/CN=$PANEL_HOST" \\
    -addext "subjectAltName=DNS:$PANEL_HOST,IP:$(hostname -I | awk '{print $1}')" \\''',
    "panel certificate hostname",
)

installer = replace_once(
    installer,
    'PHP_INSTALLED=(); mkdir -p /run/php',
    'PHP_INSTALLED=(); PHP_SKIPPED_MODULES=(); PHP_REQUIRED_SUFFIXES=(fpm cli common mysql curl mbstring xml zip gd intl bcmath soap opcache pgsql sqlite3); mkdir -p /run/php',
    "PHP module tracking",
)
installer = replace_once(
    installer,
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
    "PHP skipped module recording",
)
installer = replace_once(
    installer,
    '''printf '%s\n' "${PHP_INSTALLED[@]}" >/etc/hostpanel/php-versions
ok "PHP-FPM installed: ${PHP_INSTALLED[*]}"''',
    '''printf '%s\n' "${PHP_INSTALLED[@]}" >/etc/hostpanel/php-versions
if ((${#PHP_SKIPPED_MODULES[@]})); then
  printf '%s\n' "${PHP_SKIPPED_MODULES[@]}" | sort -u >/etc/hostpanel/php-skipped-packages
  warn "Some PHP modules were unavailable; recorded in /etc/hostpanel/php-skipped-packages"
else
  rm -f /etc/hostpanel/php-skipped-packages
fi
for version in "${PHP_INSTALLED[@]}"; do
  for suffix in "${PHP_REQUIRED_SUFFIXES[@]}"; do
    php_package_for "$version" "$suffix" >/dev/null \
      || die "PHP $version is missing required module package for $suffix"
  done
done
ok "PHP-FPM installed: ${PHP_INSTALLED[*]}"''',
    "PHP baseline enforcement",
)

installer = replace_once(
    installer,
    '''for backup in "${TREE_ROLLBACK_BACKUPS[@]}"; do
  [[ -z "$backup" ]] || rm -rf "$backup"
done''',
    '''cancel_firewall_rollback
for backup in "${TREE_ROLLBACK_BACKUPS[@]}"; do
  [[ -z "$backup" ]] || rm -rf "$backup"
done''',
    "cancel timed firewall rollback on success",
)
write("install.sh", installer)

bootstrap = read("bootstrap-install.sh")
bootstrap = replace_once(
    bootstrap,
    'PG_URL_FILE=""\n',
    '''PG_URL_FILE=""
TRUSTED_RELEASE_PUBLIC_KEY='-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAJonL5vK2NRcFkXvKZUs64ISOs+FfhwL8gQVmFO4C0qk=
-----END PUBLIC KEY-----'
''',
    "embedded release trust root",
)
bootstrap = replace_once(
    bootstrap,
    '''PUBLIC_KEY="$CHECKOUT/hostpanel-v${ARCHIVE_RELEASE_ID}-release.pub"
[[ -s "$ARCHIVE" && -s "$SIGNATURE" && -s "$PUBLIC_KEY" ]] \\
  || die "Reviewed commit lacks the complete signed source-release files"''',
    '''PUBLIC_KEY="$WORK_DIR/trusted-release.pub"
printf '%s\n' "$TRUSTED_RELEASE_PUBLIC_KEY" >"$PUBLIC_KEY"
chmod 600 "$PUBLIC_KEY"
[[ -s "$ARCHIVE" && -s "$SIGNATURE" ]] \\
  || die "Reviewed commit lacks the complete signed source-release files"''',
    "independent release key",
)
bootstrap = replace_once(
    bootstrap,
    '''# The signed archive may contain an older installer hotfix level. Preserve its
# complete application tree while running reviewed hotfixes from the same pinned
# Git commit.
install -m 0755 "$CHECKOUT/install.sh" "$SOURCE_ROOT/install.sh"''',
    '''# The embedded release key authenticates the signed base archive. The exact
# overlay below is independently authenticated by the operator-supplied full Git
# commit SHA. Verify each overlay path resolves to a regular blob in that commit
# before copying it into the extracted tree.
verify_commit_file(){
  local path="$1" expected actual
  expected="$(git -C "$CHECKOUT" rev-parse "$FETCHED_COMMIT:$path" 2>/dev/null)" \
    || die "Reviewed commit does not contain $path"
  actual="$(git -C "$CHECKOUT" hash-object "$CHECKOUT/$path")" \
    || die "Could not hash reviewed overlay $path"
  [[ "$actual" == "$expected" ]] || die "Reviewed overlay does not match commit object: $path"
}
verify_commit_file install.sh
install -m 0755 "$CHECKOUT/install.sh" "$SOURCE_ROOT/install.sh"''',
    "commit-authenticated overlay",
)
bootstrap = replace_once(
    bootstrap,
    '''INSTALLER_HOTFIX="$CHECKOUT/release-hotfixes/install/php9_probe.py"
[[ -f "$INSTALLER_HOTFIX" && ! -L "$INSTALLER_HOTFIX" ]] \\
  || die "Reviewed commit is missing the PHP 9 installer probe hotfix"''',
    '''INSTALLER_HOTFIX="$CHECKOUT/release-hotfixes/install/php9_probe.py"
verify_commit_file release-hotfixes/install/php9_probe.py
[[ -f "$INSTALLER_HOTFIX" && ! -L "$INSTALLER_HOTFIX" ]] \\
  || die "Reviewed commit is missing the PHP 9 installer probe hotfix"''',
    "verify installer hotfix object",
)
bootstrap = replace_once(
    bootstrap,
    '''DBCOMPAT_HOTFIX="$CHECKOUT/release-hotfixes/app/dbcompat.py"
[[ -f "$DBCOMPAT_HOTFIX" && ! -L "$DBCOMPAT_HOTFIX" ]]''',
    '''DBCOMPAT_HOTFIX="$CHECKOUT/release-hotfixes/app/dbcompat.py"
verify_commit_file release-hotfixes/app/dbcompat.py
[[ -f "$DBCOMPAT_HOTFIX" && ! -L "$DBCOMPAT_HOTFIX" ]]''',
    "verify app hotfix object",
)
write("bootstrap-install.sh", bootstrap)

readme = read("README.md")
readme = re.sub(r'\*\*Validated installer commit:\*\* `[0-9a-f]{40}`', '**Validated installer commit:** replace `REVIEWED_COMMIT_SHA` with an audited full commit SHA', readme)
readme = re.sub(r'https://raw\.githubusercontent\.com/1-vps/hostpanel/[0-9a-f]{40}/bootstrap-install\.sh', 'https://raw.githubusercontent.com/1-vps/hostpanel/REVIEWED_COMMIT_SHA/bootstrap-install.sh', readme)
readme = re.sub(r'HP_REPO_REF=[0-9a-f]{40}', 'HP_REPO_REF=REVIEWED_COMMIT_SHA', readme)
readme = replace_once(
    readme,
    'Do not run an unpinned `main` branch script as root. Download\n`bootstrap-install.sh` and set `HP_REPO_REF` to the same reviewed full commit\nSHA.',
    'Do not run an unpinned `main` branch script as root. Replace\n`REVIEWED_COMMIT_SHA` with a full commit SHA that you have reviewed, download\n`bootstrap-install.sh` from that exact commit, and set `HP_REPO_REF` to the\nsame value. The bootstrap uses an embedded release public key for the signed\nbase archive and the full Git object ID for the reviewed overlay.',
    "README trust instructions",
)
write("README.md", readme)

setup = read("SETUP.md")
setup = re.sub(r'```text\n[0-9a-f]{40}\n```', '```text\nREVIEWED_COMMIT_SHA\n```', setup, count=1)
setup = re.sub(r'https://raw\.githubusercontent\.com/1-vps/hostpanel/[0-9a-f]{40}/bootstrap-install\.sh', 'https://raw.githubusercontent.com/1-vps/hostpanel/REVIEWED_COMMIT_SHA/bootstrap-install.sh', setup)
setup = re.sub(r'HP_REPO_REF=[0-9a-f]{40}', 'HP_REPO_REF=REVIEWED_COMMIT_SHA', setup)
setup = setup.replace('Reinstall mode creates a root-only safety snapshot under\n`/var/backups/hostpanel/install/`', 'Reinstall mode creates a root-only safety snapshot under\n`/var/backups/hostpanel-install/`')
setup = replace_once(
    setup,
    'The bootstrap verifies the embedded signed `3.4.0-hardened-r5` source archive,\napplies the reviewed r6 repair chain from the same pinned commit, and installs\n`3.4.0-hardened-r6`.',
    'The bootstrap verifies the signed `3.4.0-hardened-r5` source archive with an\nembedded release public key. It then verifies every r6 overlay file against the\noperator-supplied full Git commit object before applying the reviewed repair\nchain and installing `3.4.0-hardened-r6`.',
    "SETUP trust description",
)
setup = replace_once(
    setup,
    '`HP_REPO_REF` must be the same full commit SHA used in the download URL.',
    '`HP_REPO_REF` must be the same reviewed full commit SHA used in the download URL.\nDo not copy an unresolved or abbreviated SHA from documentation.',
    "SETUP commit guidance",
)
write("SETUP.md", setup)

hardening_tests = r'''#!/usr/bin/env python3
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class InstallerHardeningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        cls.bootstrap = (ROOT / "bootstrap-install.sh").read_text(encoding="utf-8")
        cls.readme = (ROOT / "README.md").read_text(encoding="utf-8")
        cls.setup = (ROOT / "SETUP.md").read_text(encoding="utf-8")

    def test_root_only_snapshots_are_separate_from_panel_backups(self):
        self.assertIn('INSTALL_SNAPSHOT_DIR="/var/backups/hostpanel-install"', self.installer)
        self.assertIn('install -d -o root -g root -m 700 "$INSTALL_SNAPSHOT_DIR"', self.installer)
        self.assertNotIn('$BACKUP_DIR/install/reinstall-', self.installer)

    def test_failed_run_tracks_packages_and_restores_config(self):
        self.assertIn('NEW_PACKAGES=()', self.installer)
        self.assertIn('rollback_new_packages', self.installer)
        self.assertIn('etc/apt/sources.list.d', self.installer)
        self.assertIn('etc/firewalld etc/ufw etc/fstab', self.installer)

    def test_firewall_preserves_ssh_and_has_timed_rollback(self):
        self.assertIn("sshd -T", self.installer)
        self.assertIn('SSH_CONNECTION', self.installer)
        self.assertIn('systemd-run --quiet', self.installer)
        self.assertIn('HP_PANEL_ADMIN_CIDR', self.installer)

    def test_sensitive_password_is_not_in_python_argv(self):
        self.assertIn("password = sys.stdin.read()", self.installer)
        self.assertNotRegex(self.installer, re.compile(r"store\.init\('admin', '\$ADMIN_PASS'\)"))

    def test_redis_default_user_is_disabled(self):
        self.assertIn('user default off', self.installer)
        self.assertIn('user hostpanel on >$REDIS_PASSWORD', self.installer)
        self.assertIn('username = "hostpanel";', self.installer)

    def test_required_services_fail_closed(self):
        for diagnostic in (
            'Dovecot failed to restart',
            'Redis failed to start',
            'PostgreSQL failed to start',
            'Rspamd or Postfix failed to restart',
            'Apache failed to restart',
            'Post-install health check failed',
        ):
            self.assertIn(diagnostic, self.installer)

    def test_no_mutable_vendor_shell_execution(self):
        self.assertNotIn('bash "$LITESPEED_REPO_SCRIPT"', self.installer)
        self.assertNotIn('https://repo.litespeed.sh', self.installer)

    def test_bootstrap_has_independent_release_key_and_commit_overlay_checks(self):
        self.assertIn('TRUSTED_RELEASE_PUBLIC_KEY', self.bootstrap)
        self.assertIn('verify_commit_file', self.bootstrap)
        self.assertNotIn('PUBLIC_KEY="$CHECKOUT/', self.bootstrap)

    def test_certificate_uses_configured_panel_host(self):
        self.assertIn('-subj "/CN=$PANEL_HOST"', self.installer)
        self.assertIn('subjectAltName=DNS:$PANEL_HOST', self.installer)

    def test_docs_do_not_publish_an_unresolvable_literal_sha(self):
        self.assertIn('REVIEWED_COMMIT_SHA', self.readme)
        self.assertIn('REVIEWED_COMMIT_SHA', self.setup)
        dead = '2dee7b6326c6158392aa48693634fcabea171ba1'
        self.assertNotIn(dead, self.readme)
        self.assertNotIn(dead, self.setup)


if __name__ == "__main__":
    unittest.main()
'''
write("tests/test_installer_hardening.py", hardening_tests)

workflow = r'''name: Installer hardening

on:
  pull_request:
  push:
    branches: [main]

permissions:
  contents: read

jobs:
  static-and-runtime-lock:
    runs-on: ubuntu-26.04
    steps:
      - uses: actions/checkout@v4
      - name: Bash syntax
        run: |
          bash -n install.sh
          bash -n bootstrap-install.sh
          bash -n test-matrix.sh
          bash -n run-full-test-matrix.sh
      - name: Installer regression tests
        run: python3 -m unittest discover -s tests -p 'test_*.py' -v
      - name: ShellCheck error-level
        run: |
          sudo apt-get update -qq
          sudo apt-get install -y -qq shellcheck
          shellcheck -S error install.sh bootstrap-install.sh test-matrix.sh run-full-test-matrix.sh
      - name: Signed archive Python 3.14 lock install
        run: |
          set -euo pipefail
          archive=$(awk '$2 ~ /-source\.tar\.gz$/ {print $2}' SHA256SUMS)
          test -n "$archive"
          sha256sum -c <(grep "  $archive$" SHA256SUMS)
          mkdir release
          tar -xzf "$archive" -C release
          root=$(find release -mindepth 1 -maxdepth 1 -type d -print -quit)
          test -s "$root/requirements.lock"
          python3 -m venv /tmp/hostpanel-lock-test
          /tmp/hostpanel-lock-test/bin/pip install --quiet --upgrade 'pip==25.1.1'
          /tmp/hostpanel-lock-test/bin/pip install --quiet --require-hashes -r "$root/requirements.lock"
      - name: Ubuntu 26.04 package candidates
        run: |
          sudo apt-get update -qq
          for package in python3-venv php8.5-fpm php8.5-cli rspamd postgresql; do
            candidate=$(apt-cache policy "$package" | awk '/Candidate:/ {print $2; exit}')
            test -n "$candidate" && test "$candidate" != '(none)'
          done
'''
write(".github/workflows/installer-hardening.yml", workflow)

# Rename misleading dry-run language without pretending it is an installation.
for path in ("test-matrix.sh", "run-full-test-matrix.sh"):
    text = read(path)
    text = text.replace("MINIMAL INSTALLATION TEST", "INSTALLER DRY-RUN TEST")
    text = text.replace("minimal dry-run install", "installer dry-run")
    write(path, text)

print("deep audit hardening applied")
