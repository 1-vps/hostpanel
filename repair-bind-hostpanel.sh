#!/usr/bin/env bash
# Repair BIND configuration left by HostPanel releases that created a second
# global options {} block. Safe to run more than once.
set -euo pipefail

[[ "${EUID:-$(id -u)}" -eq 0 ]] || { echo "Run this repair as root (sudo)." >&2; exit 1; }
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
EDITOR="$SCRIPT_DIR/tools/bind_authoritative_config.py"
LOG=/var/log/hostpanel-bind-repair.log

[[ -x "$EDITOR" ]] || { echo "Missing BIND repair helper: $EDITOR" >&2; exit 1; }
[[ -d /etc/bind && ! -L /etc/bind ]] || { echo "Unsafe or missing /etc/bind directory." >&2; exit 1; }
VALIDATOR="$(command -v named-checkconf || true)"
[[ -n "$VALIDATOR" ]] || { echo "named-checkconf is missing. Install bind9-utils first." >&2; exit 1; }

TMP="$(mktemp -d /var/tmp/hostpanel-bind-repair.XXXXXXXX)"
chmod 700 "$TMP"
BACKUP="/root/hostpanel-bind-before-repair-$(date -u +%Y%m%dT%H%M%SZ).tar.gz"
UNIT=named
systemctl cat named.service >/dev/null 2>&1 || UNIT=bind9
WAS_ACTIVE=no
systemctl is-active --quiet "$UNIT" 2>/dev/null && WAS_ACTIVE=yes

cleanup(){ rm -rf -- "$TMP"; }
trap cleanup EXIT
cp -a /etc/bind "$TMP/bind"
tar -C / -czf "$BACKUP" etc/bind
chmod 600 "$BACKUP"

restore(){
  echo "Restoring the previous BIND configuration from $BACKUP" >&2
  systemctl stop "$UNIT" >>"$LOG" 2>&1 || true
  [[ -d /etc/bind && ! -L /etc/bind ]] || mkdir -p /etc/bind
  rsync -a --delete "$TMP/bind/" /etc/bind/
  if [[ "$WAS_ACTIVE" == yes ]]; then
    systemctl restart "$UNIT" >>"$LOG" 2>&1 || true
  fi
}

{
  echo "===== HostPanel BIND repair $(date -u +%Y-%m-%dT%H:%M:%SZ) ====="
  python3 "$EDITOR" --root / --validator "$VALIDATOR"
} 2>&1 | tee -a "$LOG" || {
  restore
  echo "BIND repair failed. Original files were restored. See $LOG" >&2
  exit 1
}

if ! systemctl unmask "$UNIT" >>"$LOG" 2>&1; then
  restore
  echo "Could not unmask $UNIT.service. Original files were restored." >&2
  exit 1
fi
if ! systemctl restart "$UNIT" >>"$LOG" 2>&1 \
   || ! systemctl is-active --quiet "$UNIT"; then
  {
    echo "--- systemctl status $UNIT ---"
    systemctl status "$UNIT" --no-pager -l || true
    echo "--- journalctl -u $UNIT ---"
    journalctl -u "$UNIT" -n 150 --no-pager || true
    echo "--- port 53 listeners ---"
    ss -H -lntup 2>/dev/null | awk '$5 ~ /(^|\]):53$/ || $5 ~ /:53$/ {print}' || true
  } 2>&1 | tee -a "$LOG" >&2
  restore
  echo "BIND did not start. Original files were restored. See $LOG" >&2
  exit 1
fi

printf 'BIND configuration repaired and %s.service is active.\n' "$UNIT"
printf 'Permanent pre-repair backup: %s\n' "$BACKUP"
printf 'Repair log: %s\n' "$LOG"
