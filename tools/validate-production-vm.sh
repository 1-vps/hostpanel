#!/usr/bin/env bash
# Validate a HostPanel installation on a real systemd VM.
# Read-only checks run by default. Backup, restore, and failure-injection hooks
# require explicit opt-in and independently reviewed root-owned scripts.
set -euo pipefail

usage(){
  cat <<'EOF'
Usage: sudo tools/validate-production-vm.sh [MODE]

Modes:
  --check           Run non-destructive production checks (default).
  --prepare-reboot  Run checks and record the current boot ID.
  --post-reboot     Require a changed boot ID, then rerun all checks.
  --help            Show this help.

Optional environment:
  HP_EXPECTED_VERSION             Expected installed version.
  HP_PANEL_HOST                   Panel hostname override.
  HP_PANEL_PORT                   Panel port override.
  HP_EXPECTED_PUBLIC_IP           Require panel DNS to include this address.
  HP_DESTRUCTIVE_TESTS=yes        Allow operator-supplied test hooks.
  HP_PROVIDER_SNAPSHOT_CONFIRMED=yes
                                  Required for restore/failure-injection hooks.
  HP_BACKUP_TEST_SCRIPT           Absolute path to a root-owned executable.
  HP_RESTORE_TEST_SCRIPT          Absolute path to a root-owned executable.
  HP_FAILURE_INJECTION_SCRIPT     Absolute path to a root-owned executable.
EOF
}

MODE="${1:---check}"
case "$MODE" in
  --check|--prepare-reboot|--post-reboot) ;;
  --help|-h) usage; exit 0 ;;
  *) printf 'Unknown mode: %s\n' "$MODE" >&2; usage >&2; exit 2 ;;
esac

[[ ${EUID:-$(id -u)} -eq 0 ]] || {
  printf 'Error: run this validator as root.\n' >&2
  exit 1
}

EXPECTED_VERSION="${HP_EXPECTED_VERSION:-3.4.0-hardened-r6}"
STATE_DIR="${HP_VALIDATION_STATE_DIR:-/var/lib/hostpanel-validation}"
REPORT_DIR="${HP_VALIDATION_REPORT_DIR:-/var/log}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REPORT="$REPORT_DIR/hostpanel-production-validation-$TIMESTAMP.log"
PRE_REBOOT_STATE="$STATE_DIR/pre-reboot.boot-id"
PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0

install -d -o root -g root -m 700 "$STATE_DIR"
install -d -o root -g root -m 755 "$REPORT_DIR"
touch "$REPORT"
chown root:root "$REPORT"
chmod 600 "$REPORT"
exec > >(tee -a "$REPORT") 2>&1

pass(){
  PASS_COUNT=$((PASS_COUNT + 1))
  printf '[PASS] %s\n' "$*"
}

warn(){
  WARN_COUNT=$((WARN_COUNT + 1))
  printf '[WARN] %s\n' "$*"
}

fail(){
  FAIL_COUNT=$((FAIL_COUNT + 1))
  printf '[FAIL] %s\n' "$*"
}

run_required(){
  local label="$1"
  shift
  if "$@"; then
    pass "$label"
  else
    fail "$label"
  fi
}

read_config_value(){
  local key="$1" file="/opt/hostpanel/config.env" value=""
  [[ -r "$file" ]] || return 1
  value="$(awk -F= -v wanted="$key" '$1 == wanted {sub(/^[^=]*=/, ""); print; exit}' "$file")"
  value="${value%\"}"
  value="${value#\"}"
  value="${value%\'}"
  value="${value#\'}"
  [[ -n "$value" ]] || return 1
  printf '%s\n' "$value"
}

unit_exists(){
  local unit="$1"
  systemctl list-unit-files "${unit}.service" --no-legend 2>/dev/null | grep -q .
}

check_unit(){
  local unit="$1" enabled_state=""
  unit_exists "$unit" || return 0
  if systemctl is-active --quiet "${unit}.service"; then
    pass "${unit}.service is active"
  else
    fail "${unit}.service is not active"
  fi
  enabled_state="$(systemctl is-enabled "${unit}.service" 2>/dev/null || true)"
  case "$enabled_state" in
    enabled|static|indirect|generated|alias|linked|linked-runtime|transient)
      pass "${unit}.service has acceptable boot activation state: $enabled_state"
      ;;
    *)
      warn "${unit}.service boot activation state is '${enabled_state:-unknown}'"
      ;;
  esac
}

check_systemd(){
  local init_name=""
  init_name="$(cat /proc/1/comm 2>/dev/null || true)"
  if [[ "$init_name" == systemd ]]; then
    pass "systemd is PID 1"
  else
    fail "systemd is not PID 1"
    return
  fi

  local failed_units=""
  failed_units="$(systemctl --failed --no-legend --plain 2>/dev/null || true)"
  if [[ -z "$failed_units" ]]; then
    pass "no failed systemd units"
  else
    fail "systemd has failed units"
    printf '%s\n' "$failed_units"
  fi

  local unit
  for unit in \
    hostpanel nginx apache2 httpd postgresql redis-server redis dovecot rspamd \
    postfix exim4 named bind9 fail2ban firewalld; do
    check_unit "$unit"
  done
}

check_supported_os(){
  local id="" version=""
  if [[ -r /etc/os-release ]]; then
    id="$(awk -F= '$1 == "ID" {gsub(/\"/, "", $2); print $2}' /etc/os-release)"
    version="$(awk -F= '$1 == "VERSION_ID" {gsub(/\"/, "", $2); print $2}' /etc/os-release)"
  fi
  case "$id:$version" in
    ubuntu:22.04|ubuntu:24.04|ubuntu:26.04|debian:12|debian:13|rocky:9*|rocky:10*|almalinux:9*|almalinux:10*)
      pass "supported operating system detected: $id $version"
      ;;
    *)
      fail "unsupported or unidentified operating system: ${id:-unknown} ${version:-unknown}"
      ;;
  esac
}

check_version(){
  local actual=""
  if [[ -r /opt/hostpanel/VERSION ]]; then
    actual="$(tr -d '[:space:]' </opt/hostpanel/VERSION)"
  fi
  if [[ "$actual" == "$EXPECTED_VERSION" ]]; then
    pass "installed version is $EXPECTED_VERSION"
  else
    fail "installed version '${actual:-missing}' does not match $EXPECTED_VERSION"
  fi
}

check_path_security(){
  local path="$1" expected_owner="$2" required_mode="${3:-}" owner="" mode="" mode_value=0
  if [[ ! -e "$path" ]]; then
    fail "$path is missing"
    return
  fi
  owner="$(stat -c '%U' "$path")"
  mode="$(stat -c '%a' "$path")"
  if [[ "$owner" == "$expected_owner" ]]; then
    pass "$path owner is $expected_owner"
  else
    fail "$path owner is $owner, expected $expected_owner"
  fi
  if [[ -n "$required_mode" ]]; then
    if [[ "$mode" == "$required_mode" ]]; then
      pass "$path mode is $required_mode"
    else
      fail "$path mode is $mode, expected $required_mode"
    fi
  else
    mode_value=$((8#$mode))
    if (( (mode_value & 0022) == 0 )); then
      pass "$path is not group/world writable"
    else
      fail "$path is group/world writable (mode $mode)"
    fi
  fi
}

check_filesystem_security(){
  check_path_security /var/backups/hostpanel-install root 700
  check_path_security /opt/hostpanel/config.env root
  check_path_security /etc/hostpanel root
  if [[ -d /opt/hostpanel/credentials ]]; then
    check_path_security /opt/hostpanel/credentials root
  else
    warn "/opt/hostpanel/credentials is absent"
  fi
}

check_doctor(){
  local python_bin="/opt/hostpanel/venv/bin/python"
  local doctor="/opt/hostpanel/app/hostpanel-doctor"
  if [[ -x "$python_bin" && -f "$doctor" ]]; then
    run_required "hostpanel-doctor passed" "$python_bin" "$doctor" --quiet
  else
    fail "hostpanel-doctor runtime is missing"
  fi
}

check_service_configs(){
  command -v nginx >/dev/null 2>&1 && run_required "nginx configuration is valid" nginx -t
  command -v apache2ctl >/dev/null 2>&1 && run_required "Apache configuration is valid" apache2ctl configtest
  if ! command -v apache2ctl >/dev/null 2>&1 && command -v httpd >/dev/null 2>&1; then
    run_required "Apache configuration is valid" httpd -t
  fi
  command -v postfix >/dev/null 2>&1 && run_required "Postfix configuration is valid" postfix check
  command -v dovecot >/dev/null 2>&1 && run_required "Dovecot configuration is readable" dovecot -n
  command -v rspamadm >/dev/null 2>&1 && run_required "Rspamd configuration is valid" rspamadm configtest
  command -v named-checkconf >/dev/null 2>&1 && run_required "DNS configuration is valid" named-checkconf
}

listener_present(){
  local port="$1"
  ss -lntH 2>/dev/null | awk -v wanted=":$port" '$4 ~ wanted "$" {found=1} END {exit !found}'
}

check_listeners(){
  local panel_port="${HP_PANEL_PORT:-}" panel_host="${HP_PANEL_HOST:-}" ssh_ports="" port=""
  [[ -n "$panel_port" ]] || panel_port="$(read_config_value HP_PANEL_PORT 2>/dev/null || true)"
  [[ -n "$panel_port" ]] || panel_port="$(read_config_value PANEL_PORT 2>/dev/null || true)"
  [[ -n "$panel_port" ]] || panel_port=2222
  [[ -n "$panel_host" ]] || panel_host="$(read_config_value HP_PANEL_HOST 2>/dev/null || true)"
  [[ -n "$panel_host" ]] || panel_host="$(read_config_value PANEL_HOST 2>/dev/null || true)"

  if listener_present "$panel_port"; then
    pass "panel port $panel_port is listening"
  else
    fail "panel port $panel_port is not listening"
  fi

  ssh_ports="$(sshd -T 2>/dev/null | awk '$1 == "port" {print $2}' | sort -u || true)"
  [[ -n "$ssh_ports" ]] || ssh_ports=22
  while IFS= read -r port; do
    [[ -n "$port" ]] || continue
    if listener_present "$port"; then
      pass "SSH port $port is listening"
    else
      fail "SSH port $port is not listening"
    fi
  done <<<"$ssh_ports"

  if command -v curl >/dev/null 2>&1; then
    if curl -ksS --max-time 8 -o /dev/null "https://127.0.0.1:${panel_port}/" \
      || curl -sS --max-time 8 -o /dev/null "http://127.0.0.1:${panel_port}/"; then
      pass "panel endpoint responds locally on port $panel_port"
    else
      fail "panel endpoint does not respond locally on port $panel_port"
    fi
  else
    warn "curl is unavailable; panel HTTP response was not tested"
  fi

  if [[ -n "$panel_host" ]]; then
    if getent ahosts "$panel_host" >/dev/null 2>&1; then
      pass "panel hostname resolves: $panel_host"
    else
      fail "panel hostname does not resolve: $panel_host"
    fi
    if [[ -n "${HP_EXPECTED_PUBLIC_IP:-}" ]]; then
      if getent ahosts "$panel_host" | awk '{print $1}' | grep -Fxq "$HP_EXPECTED_PUBLIC_IP"; then
        pass "$panel_host resolves to expected address $HP_EXPECTED_PUBLIC_IP"
      else
        fail "$panel_host does not resolve to expected address $HP_EXPECTED_PUBLIC_IP"
      fi
    fi
  else
    warn "panel hostname was not found in environment or config.env"
  fi
}

check_firewall(){
  if command -v firewall-cmd >/dev/null 2>&1; then
    if firewall-cmd --state >/dev/null 2>&1; then
      pass "firewalld is active"
      firewall-cmd --list-all || fail "firewalld rules could not be listed"
    else
      fail "firewalld is installed but inactive"
    fi
  elif command -v ufw >/dev/null 2>&1; then
    local status=""
    status="$(ufw status 2>/dev/null || true)"
    if grep -q '^Status: active' <<<"$status"; then
      pass "ufw is active"
      printf '%s\n' "$status"
    else
      fail "ufw is installed but inactive"
    fi
  else
    fail "neither firewalld nor ufw is available"
  fi
}

check_redis_acl(){
  local redis_paths=(/etc/redis /etc/redis.conf /etc/hostpanel)
  if grep -RhsE '^[[:space:]]*user[[:space:]]+default[[:space:]]+off([[:space:]]|$)' "${redis_paths[@]}" 2>/dev/null | grep -q .; then
    pass "Redis default ACL user is disabled"
  else
    fail "Redis default ACL user disablement was not found"
  fi
  if grep -RhsE '^[[:space:]]*user[[:space:]]+hostpanel[[:space:]]+on([[:space:]]|$)' "${redis_paths[@]}" 2>/dev/null | grep -q .; then
    pass "Redis hostpanel ACL user is configured"
  else
    fail "Redis hostpanel ACL user was not found"
  fi
}

check_mail_listeners(){
  if unit_exists postfix || unit_exists exim4; then
    if listener_present 25; then
      pass "SMTP port 25 is listening"
    else
      fail "mail role is present but SMTP port 25 is not listening"
    fi
  fi
  if unit_exists dovecot; then
    if listener_present 143 || listener_present 993; then
      pass "IMAP service is listening"
    else
      fail "Dovecot is present but neither port 143 nor 993 is listening"
    fi
  fi
}

check_certificate(){
  local certificate=""
  if command -v openssl >/dev/null 2>&1; then
    certificate="$(find /opt/hostpanel/tls /etc/hostpanel -type f \( -name '*.crt' -o -name '*.pem' \) -print -quit 2>/dev/null || true)"
  fi
  if [[ -n "$certificate" ]]; then
    if openssl x509 -in "$certificate" -noout -checkend 86400 >/dev/null 2>&1; then
      pass "certificate is parseable and valid for at least 24 hours: $certificate"
    else
      fail "certificate is invalid or expires within 24 hours: $certificate"
    fi
  else
    warn "no HostPanel certificate was found for expiry validation"
  fi
}

validate_hook_script(){
  local path="$1" owner="" mode="" mode_value=0
  case "$path" in
    /*) ;;
    *) fail "hook path must be absolute: $path"; return 1 ;;
  esac
  if [[ ! -f "$path" || ! -x "$path" || -L "$path" ]]; then
    fail "hook must be a regular executable, non-symlink file: $path"
    return 1
  fi
  owner="$(stat -c '%U' "$path")"
  mode="$(stat -c '%a' "$path")"
  mode_value=$((8#$mode))
  if [[ "$owner" != root || (mode_value & 0022) -ne 0 ]]; then
    fail "hook must be root-owned and not group/world writable: $path"
    return 1
  fi
  return 0
}

run_hook(){
  local label="$1" path="$2"
  if validate_hook_script "$path"; then
    run_required "$label" "$path"
  fi
}

run_operator_hooks(){
  local backup="${HP_BACKUP_TEST_SCRIPT:-}"
  local restore="${HP_RESTORE_TEST_SCRIPT:-}"
  local failure="${HP_FAILURE_INJECTION_SCRIPT:-}"
  if [[ -z "$backup$restore$failure" ]]; then
    warn "backup, restore, and failure-injection hooks were not supplied"
    return
  fi
  if [[ "${HP_DESTRUCTIVE_TESTS:-no}" != yes ]]; then
    fail "operator hooks were supplied without HP_DESTRUCTIVE_TESTS=yes"
    return
  fi
  [[ -z "$backup" ]] || run_hook "operator backup test passed" "$backup"
  if [[ -n "$restore$failure" && "${HP_PROVIDER_SNAPSHOT_CONFIRMED:-no}" != yes ]]; then
    fail "restore/failure-injection hooks require HP_PROVIDER_SNAPSHOT_CONFIRMED=yes"
    return
  fi
  [[ -z "$restore" ]] || run_hook "operator restore test passed" "$restore"
  [[ -z "$failure" ]] || run_hook "operator failure-injection rollback test passed" "$failure"
}

verify_reboot_transition(){
  local previous="" current=""
  if [[ ! -r "$PRE_REBOOT_STATE" ]]; then
    fail "pre-reboot state is missing; run --prepare-reboot first"
    return
  fi
  previous="$(tr -d '[:space:]' <"$PRE_REBOOT_STATE")"
  current="$(tr -d '[:space:]' </proc/sys/kernel/random/boot_id)"
  if [[ -n "$previous" && "$previous" != "$current" ]]; then
    pass "boot ID changed; reboot was verified"
  else
    fail "boot ID did not change; reboot was not verified"
  fi
}

run_all_checks(){
  check_supported_os
  check_systemd
  check_version
  check_filesystem_security
  check_doctor
  check_service_configs
  check_listeners
  check_firewall
  check_redis_acl
  check_mail_listeners
  check_certificate
  run_operator_hooks
}

printf 'HostPanel production VM validation\n'
printf 'Mode: %s\n' "$MODE"
printf 'Report: %s\n' "$REPORT"
printf 'UTC start: %s\n\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"

if [[ "$MODE" == --post-reboot ]]; then
  verify_reboot_transition
fi

run_all_checks

if [[ "$MODE" == --prepare-reboot && $FAIL_COUNT -eq 0 ]]; then
  tr -d '[:space:]' </proc/sys/kernel/random/boot_id >"$PRE_REBOOT_STATE"
  chown root:root "$PRE_REBOOT_STATE"
  chmod 600 "$PRE_REBOOT_STATE"
  pass "pre-reboot boot ID recorded at $PRE_REBOOT_STATE"
fi

printf '\nSummary: %d passed, %d warnings, %d failed\n' "$PASS_COUNT" "$WARN_COUNT" "$FAIL_COUNT"
printf 'Report saved to %s\n' "$REPORT"

if ((FAIL_COUNT > 0)); then
  exit 1
fi
exit 0
