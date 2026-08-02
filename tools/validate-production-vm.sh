#!/usr/bin/env bash
# Validate a HostPanel installation on a real systemd VM.
# Normal checks are read-only. Operator hooks require explicit opt-in.
set -euo pipefail

usage(){
  cat <<'HELP'
Usage: sudo validate-production-vm.sh [--check|--prepare-reboot|--post-reboot]

Optional environment:
  HP_EXPECTED_VERSION, HP_PANEL_HOST, HP_PANEL_PORT, HP_EXPECTED_PUBLIC_IP
  HP_VALIDATION_IO_HELPER, HP_VALIDATION_STATE_DIR, HP_VALIDATION_REPORT_DIR
  HP_DESTRUCTIVE_TESTS=yes
  HP_PROVIDER_INSTANCE_ID, HP_PROVIDER_SNAPSHOT_ID, HP_PROVIDER_SNAPSHOT_CREATED_AT_UTC
  HP_BACKUP_TEST_SCRIPT, HP_RESTORE_TEST_SCRIPT, HP_FAILURE_INJECTION_SCRIPT
HELP
}

MODE="${1:---check}"
case "$MODE" in
  --check|--prepare-reboot|--post-reboot) ;;
  --help|-h) usage; exit 0 ;;
  *) printf 'Unknown mode: %s\n' "$MODE" >&2; usage >&2; exit 2 ;;
esac

[[ ${EUID:-$(id -u)} -eq 0 ]] || { printf 'Error: run as root.\n' >&2; exit 1; }

SCRIPT_PATH="$(readlink -f -- "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$SCRIPT_PATH")" && pwd -P)"
VALIDATION_IO="${HP_VALIDATION_IO_HELPER:-$SCRIPT_DIR/secure-validation-io.py}"
PYTHON3="$(command -v python3)"
BASH_BIN="$(command -v bash)"
[[ -n "$PYTHON3" && -n "$BASH_BIN" ]] || {
  printf '%s\n' 'Error: python3 and bash are required.' >&2
  exit 1
}
[[ -f "$VALIDATION_IO" && ! -L "$VALIDATION_IO" ]] || {
  printf 'Error: secure validation helper is unsafe or missing: %s\n' "$VALIDATION_IO" >&2
  exit 1
}
helper_metadata="$(stat -c '%u:%a:%h' "$VALIDATION_IO")"
helper_uid="${helper_metadata%%:*}"
helper_rest="${helper_metadata#*:}"
helper_mode="${helper_rest%%:*}"
helper_links="${helper_rest##*:}"
helper_mode_value=$((8#$helper_mode))
if [[ "$helper_uid" != 0 || "$helper_links" != 1 ]] || ((helper_mode_value & 0022)); then
  printf 'Error: secure validation helper must be root-owned, single-linked, and not group/world writable.\n' >&2
  exit 1
fi

EXPECTED_VERSION="${HP_EXPECTED_VERSION:-3.4.0}"
STATE_DIR="${HP_VALIDATION_STATE_DIR:-/var/lib/hostpanel-validation}"
REPORT_DIR="${HP_VALIDATION_REPORT_DIR:-$STATE_DIR/reports}"
REBOOT_STATE="$STATE_DIR/pre-reboot.boot-id"
"$PYTHON3" -I "$VALIDATION_IO" ensure-directory "$STATE_DIR" 700
"$PYTHON3" -I "$VALIDATION_IO" ensure-directory "$REPORT_DIR" 700

if [[ "${HP_VALIDATION_REPORT_ACTIVE:-no}" != yes ]]; then
  exec "$PYTHON3" -I "$VALIDATION_IO" run-with-report "$REPORT_DIR" -- \
    "$BASH_BIN" "$SCRIPT_PATH" "$@"
fi
REPORT="${HP_VALIDATION_REPORT_PATH:?secure report path is missing}"

PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0

pass(){ PASS_COUNT=$((PASS_COUNT + 1)); printf '[PASS] %s\n' "$*"; }
warn(){ WARN_COUNT=$((WARN_COUNT + 1)); printf '[WARN] %s\n' "$*"; }
fail(){ FAIL_COUNT=$((FAIL_COUNT + 1)); printf '[FAIL] %s\n' "$*"; }
run_required(){ local label="$1"; shift; if "$@"; then pass "$label"; else fail "$label"; fi; }

config_value(){
  local key="$1" value="" file=/opt/hostpanel/config.env
  [[ -r "$file" ]] || return 1
  value="$(awk -F= -v wanted="$key" '$1 == wanted {sub(/^[^=]*=/, ""); print; exit}' "$file")"
  value="${value%\"}"; value="${value#\"}"; value="${value%\'}"; value="${value#\'}"
  [[ -n "$value" ]] || return 1
  printf '%s\n' "$value"
}

unit_exists(){ systemctl list-unit-files "$1.service" --no-legend 2>/dev/null | grep -q .; }
check_unit(){
  local unit="$1" state=""
  unit_exists "$unit" || return 0
  systemctl is-active --quiet "$unit.service" && pass "$unit.service is active" || fail "$unit.service is not active"
  state="$(systemctl is-enabled "$unit.service" 2>/dev/null || true)"
  case "$state" in
    enabled|static|indirect|generated|alias|linked|linked-runtime|transient) pass "$unit.service boot state: $state" ;;
    *) warn "$unit.service boot state: ${state:-unknown}" ;;
  esac
}

check_systemd(){
  local failed="" unit=""
  [[ "$(cat /proc/1/comm 2>/dev/null || true)" == systemd ]] && pass 'systemd is PID 1' || fail 'systemd is not PID 1'
  failed="$(systemctl --failed --no-legend --plain 2>/dev/null || true)"
  if [[ -z "$failed" ]]; then pass 'no failed systemd units'; else fail 'systemd has failed units'; printf '%s\n' "$failed"; fi
  for unit in hostpanel nginx apache2 httpd lsws postgresql redis-server redis dovecot rspamd postfix exim4 named bind9 fail2ban firewalld; do
    check_unit "$unit"
  done
  if [[ -x /usr/local/lsws/bin/openlitespeed ]] && ! unit_exists lsws; then
    fail 'OpenLiteSpeed is installed but lsws.service is missing'
  fi
}

check_os(){
  local id="" version=""
  [[ -r /etc/os-release ]] && id="$(awk -F= '$1=="ID" {gsub(/"/,"",$2); print $2}' /etc/os-release)"
  [[ -r /etc/os-release ]] && version="$(awk -F= '$1=="VERSION_ID" {gsub(/"/,"",$2); print $2}' /etc/os-release)"
  case "$id:$version" in
    ubuntu:22.04|ubuntu:24.04|ubuntu:26.04|debian:12|debian:13|rocky:9*|rocky:10*|almalinux:9*|almalinux:10*) pass "supported OS: $id $version" ;;
    *) fail "unsupported or unidentified OS: ${id:-unknown} ${version:-unknown}" ;;
  esac
}

check_version(){
  local actual=""
  [[ -r /opt/hostpanel/VERSION ]] && actual="$(tr -d '[:space:]' </opt/hostpanel/VERSION)"
  [[ "$actual" == "$EXPECTED_VERSION" ]] && pass "installed version is $EXPECTED_VERSION" || fail "installed version '${actual:-missing}' does not match $EXPECTED_VERSION"
}

check_path(){
  local path="$1" exact_mode="${2:-}" owner="" mode="" value=0
  [[ -e "$path" ]] || { fail "$path is missing"; return; }
  owner="$(stat -c '%U' "$path")"; mode="$(stat -c '%a' "$path")"
  [[ "$owner" == root ]] && pass "$path owner is root" || fail "$path owner is $owner"
  if [[ -n "$exact_mode" ]]; then
    [[ "$mode" == "$exact_mode" ]] && pass "$path mode is $exact_mode" || fail "$path mode is $mode, expected $exact_mode"
  else
    value=$((8#$mode))
    (( (value & 0022) == 0 )) && pass "$path is not group/world writable" || fail "$path is group/world writable (mode $mode)"
  fi
}

check_files(){
  check_path /var/backups/hostpanel-install 700
  check_path /opt/hostpanel/config.env
  check_path /etc/hostpanel
  [[ ! -d /opt/hostpanel/credentials ]] || check_path /opt/hostpanel/credentials
}

check_doctor(){
  local py=/opt/hostpanel/venv/bin/python doctor=/opt/hostpanel/app/hostpanel-doctor rc=0
  if [[ ! -x "$py" || ! -f "$doctor" ]]; then
    fail 'hostpanel-doctor runtime is missing'
    return
  fi
  "$py" "$doctor" --quiet || rc=$?
  case "$rc" in
    0) pass 'hostpanel-doctor passed' ;;
    1) warn 'hostpanel-doctor completed with warnings' ;;
    *) fail "hostpanel-doctor failed with exit status $rc" ;;
  esac
}

check_configs(){
  command -v nginx >/dev/null 2>&1 && run_required 'nginx configuration is valid' nginx -t
  command -v apache2ctl >/dev/null 2>&1 && run_required 'Apache configuration is valid' apache2ctl configtest
  if ! command -v apache2ctl >/dev/null 2>&1 && command -v httpd >/dev/null 2>&1; then run_required 'Apache configuration is valid' httpd -t; fi
  command -v postfix >/dev/null 2>&1 && run_required 'Postfix configuration is valid' postfix check
  command -v dovecot >/dev/null 2>&1 && run_required 'Dovecot configuration is readable' dovecot -n
  command -v rspamadm >/dev/null 2>&1 && run_required 'Rspamd configuration is valid' rspamadm configtest
  command -v named-checkconf >/dev/null 2>&1 && run_required 'DNS configuration is valid' named-checkconf
}

listener(){ local port="$1"; ss -lntH 2>/dev/null | awk -v wanted=":$port" '$4 ~ wanted "$" {found=1} END {exit !found}'; }
check_listeners(){
  local panel_port="${HP_PANEL_PORT:-}" panel_host="${HP_PANEL_HOST:-}" ssh_ports="" port=""
  [[ -n "$panel_port" ]] || panel_port="$(config_value HP_PANEL_PORT 2>/dev/null || true)"
  [[ -n "$panel_port" ]] || panel_port="$(config_value PANEL_PORT 2>/dev/null || true)"
  [[ -n "$panel_port" ]] || panel_port=2222
  [[ -n "$panel_host" ]] || panel_host="$(config_value HP_PANEL_HOST 2>/dev/null || true)"
  [[ -n "$panel_host" ]] || panel_host="$(config_value PANEL_HOST 2>/dev/null || true)"
  listener "$panel_port" && pass "panel port $panel_port is listening" || fail "panel port $panel_port is not listening"
  ssh_ports="$(sshd -T 2>/dev/null | awk '$1=="port" {print $2}' | sort -u || true)"; [[ -n "$ssh_ports" ]] || ssh_ports=22
  while IFS= read -r port; do [[ -n "$port" ]] || continue; listener "$port" && pass "SSH port $port is listening" || fail "SSH port $port is not listening"; done <<<"$ssh_ports"
  if command -v curl >/dev/null 2>&1; then
    if curl -ksS --max-time 8 -o /dev/null "https://127.0.0.1:$panel_port/" || curl -sS --max-time 8 -o /dev/null "http://127.0.0.1:$panel_port/"; then pass "panel endpoint responds locally on $panel_port"; else fail "panel endpoint does not respond locally on $panel_port"; fi
  else warn 'curl is unavailable'; fi
  if [[ -n "$panel_host" ]]; then
    getent ahosts "$panel_host" >/dev/null 2>&1 && pass "panel hostname resolves: $panel_host" || fail "panel hostname does not resolve: $panel_host"
    if [[ -n "${HP_EXPECTED_PUBLIC_IP:-}" ]]; then getent ahosts "$panel_host" | awk '{print $1}' | grep -Fxq "$HP_EXPECTED_PUBLIC_IP" && pass 'panel DNS includes expected address' || fail 'panel DNS lacks expected address'; fi
  else warn 'panel hostname was not found'; fi
}

check_firewall(){
  if command -v firewall-cmd >/dev/null 2>&1; then
    firewall-cmd --state >/dev/null 2>&1 && pass 'firewalld is active' || fail 'firewalld is inactive'
    firewall-cmd --list-all || fail 'firewalld rules could not be listed'
  elif command -v ufw >/dev/null 2>&1; then
    local status=""; status="$(ufw status 2>/dev/null || true)"
    grep -q '^Status: active' <<<"$status" && pass 'ufw is active' || fail 'ufw is inactive'; printf '%s\n' "$status"
  else fail 'neither firewalld nor ufw is available'; fi
}

check_redis_acl(){
  local cli="" password_file=/opt/hostpanel/credentials/redis-password password=""
  local default_acl="" hostpanel_acl=""
  cli="$(command -v redis-cli 2>/dev/null || true)"
  [[ -n "$cli" ]] || { fail 'redis-cli is unavailable'; return; }
  [[ -r "$password_file" ]] || { fail 'Redis credential file is unavailable'; return; }
  password="$(head -1 "$password_file")"
  [[ -n "$password" ]] || { fail 'Redis credential file is empty'; return; }
  if ! default_acl="$(REDISCLI_AUTH="$password" "$cli" --no-auth-warning --user hostpanel ACL GETUSER default 2>/dev/null)"; then
    fail 'Redis ACL could not be queried through the hostpanel identity'
    return
  fi
  if ! hostpanel_acl="$(REDISCLI_AUTH="$password" "$cli" --no-auth-warning --user hostpanel ACL GETUSER hostpanel 2>/dev/null)"; then
    fail 'Redis hostpanel ACL identity could not be queried'
    return
  fi
  grep -Fxq off <<<"$default_acl" && pass 'Redis default ACL user is disabled' || fail 'Redis default ACL user is not disabled'
  grep -Fxq on <<<"$hostpanel_acl" && pass 'Redis hostpanel ACL user is enabled' || fail 'Redis hostpanel ACL user is not enabled'
}

check_mail(){
  if unit_exists postfix || unit_exists exim4; then listener 25 && pass 'SMTP port 25 is listening' || fail 'SMTP port 25 is not listening'; fi
  if unit_exists dovecot; then
    if listener 143 || listener 993; then pass 'IMAP service is listening'; else fail 'IMAP service is not listening'; fi
  fi
}

check_cert(){
  local cert=""
  command -v openssl >/dev/null 2>&1 && cert="$(find /opt/hostpanel/tls /etc/hostpanel -type f \( -name '*.crt' -o -name '*.pem' \) -print -quit 2>/dev/null || true)"
  if [[ -n "$cert" ]]; then openssl x509 -in "$cert" -noout -checkend 86400 >/dev/null 2>&1 && pass "certificate is parseable: $cert" || fail "certificate is invalid or near expiry: $cert"; else warn 'no HostPanel certificate was found'; fi
}

run_hook(){
  local label="$1" path="$2"
  if "$PYTHON3" -I "$VALIDATION_IO" exec-hook "$path"; then
    pass "$label"
  else
    fail "$label"
  fi
}
run_hooks(){
  local backup="${HP_BACKUP_TEST_SCRIPT:-}" restore="${HP_RESTORE_TEST_SCRIPT:-}" injection="${HP_FAILURE_INJECTION_SCRIPT:-}"
  local instance="${HP_PROVIDER_INSTANCE_ID:-}" snapshot="${HP_PROVIDER_SNAPSHOT_ID:-}" created="${HP_PROVIDER_SNAPSHOT_CREATED_AT_UTC:-}"
  [[ -n "$backup$restore$injection" ]] || { warn 'backup, restore, and failure-injection hooks were not supplied'; return; }
  [[ "${HP_DESTRUCTIVE_TESTS:-no}" == yes ]] || { fail 'hooks require HP_DESTRUCTIVE_TESTS=yes'; return; }
  [[ -z "$backup" ]] || run_hook 'operator backup test passed' "$backup"
  if [[ -n "$restore$injection" ]]; then
    [[ "$instance" =~ ^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$ \
      && "$snapshot" =~ ^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$ \
      && "$created" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ ]] || {
      fail 'restore/failure injection requires bound provider instance and snapshot identity'
      return
    }
  fi
  [[ -z "$restore" ]] || run_hook 'operator restore test passed' "$restore"
  [[ -z "$injection" ]] || run_hook 'operator failure-injection rollback test passed' "$injection"
}

check_reboot(){
  local previous="" current=""
  if ! previous="$("$PYTHON3" -I "$VALIDATION_IO" read-state "$REBOOT_STATE" | tr -d '[:space:]')"; then
    fail 'pre-reboot state is missing or unsafe'
    return
  fi
  current="$(tr -d '[:space:]' </proc/sys/kernel/random/boot_id)"
  [[ -n "$previous" && "$previous" != "$current" ]] && pass 'boot ID changed; reboot was verified' || fail 'boot ID did not change'
}

run_checks(){ check_os; check_systemd; check_version; check_files; check_doctor; check_configs; check_listeners; check_firewall; check_redis_acl; check_mail; check_cert; run_hooks; }

printf 'HostPanel production VM validation\nMode: %s\nReport: %s\nUTC start: %s\n\n' "$MODE" "$REPORT" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
[[ "$MODE" != --post-reboot ]] || check_reboot
run_checks
if [[ "$MODE" == --prepare-reboot && $FAIL_COUNT -eq 0 ]]; then
  boot_id="$(tr -d '[:space:]' </proc/sys/kernel/random/boot_id)"
  if printf '%s\n' "$boot_id" | "$PYTHON3" -I "$VALIDATION_IO" write-state "$REBOOT_STATE"; then
    pass "pre-reboot boot ID recorded at $REBOOT_STATE"
  else
    fail 'pre-reboot boot ID could not be recorded securely'
  fi
fi
printf '\nSummary: %d passed, %d warnings, %d failed\nReport saved to %s\n' "$PASS_COUNT" "$WARN_COUNT" "$FAIL_COUNT" "$REPORT"
((FAIL_COUNT == 0))
