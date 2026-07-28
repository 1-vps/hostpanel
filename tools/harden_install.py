#!/usr/bin/env python3
"""Run the deterministic installer hardening transform with compatibility fixes."""
from __future__ import annotations

import importlib.util
import pathlib
import sys

MODULE_PATH = pathlib.Path(__file__).with_name("harden_install_runtime.py")
SPEC = importlib.util.spec_from_file_location("hostpanel_hardener", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise SystemExit("could not load the installer hardening implementation")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

_original_module_replace_once = MODULE.replace_once
_original_regex_once = MODULE.regex_once


def _module_replace_once(text: str, old: str, new: str, label: str) -> str:
    # The rollback arrays are initialized near startup and cleared after a
    # successful install. Only the startup occurrence receives additional state.
    if label == "rollback state":
        count = text.count(old)
        if count < 1:
            raise SystemExit(f"{label}: expected at least one match, found {count}")
        return text.replace(old, new, 1)
    return _original_module_replace_once(text, old, new, label)


def _replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def _regex_once(text: str, pattern: str, replacement: str, label: str) -> str:
    # The external-repository replacement intentionally renames the section
    # marker before the firewall helper is injected. Keep the later fail-closed
    # match aligned with the transformed marker.
    if label == "timed firewall rollback helper":
        pattern = pattern.replace(
            "# ---- Enterprise Linux repositories",
            "# ---- External repositories",
        )
        replacement = replacement.replace(
            "# ---- Enterprise Linux repositories",
            "# ---- External repositories",
        )
    return _original_regex_once(text, pattern, replacement, label)


MODULE.replace_once = _module_replace_once
MODULE.regex_once = _regex_once


def compatibility_hardening(text: str) -> str:
    # Remove newly installed packages before restoring saved configuration;
    # package removal scripts must not delete files that were just restored.
    text = _replace_once(
        text,
        '''      remove_paths_absent_before_install
      if [[ -s "$REINSTALL_SNAPSHOT" ]]; then
        tar -C / -xzf "$REINSTALL_SNAPSHOT" >>"$LOG" 2>&1 || true
        command -v systemctl >/dev/null 2>&1 && systemctl daemon-reload >>"$LOG" 2>&1 || true
      fi
      rollback_new_packages''',
        '''      rollback_new_packages
      remove_paths_absent_before_install
      if [[ -s "$REINSTALL_SNAPSHOT" ]]; then
        tar -C / -xzf "$REINSTALL_SNAPSHOT" >>"$LOG" 2>&1 || true
        command -v systemctl >/dev/null 2>&1 && systemctl daemon-reload >>"$LOG" 2>&1 || true
      fi''',
        "rollback ordering",
    )

    # Reject a missing or malformed administrative source during preflight,
    # before package or firewall mutation begins.
    text = _replace_once(
        text,
        '''[[ "$PREFLIGHT_HOST" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,}$ ]] \\
  || die "Configure a valid FQDN or set HP_PANEL_HOST before installation"

if [[ "$REINSTALL" != yes && -f "$PANEL_DIR/config.env" ]]; then''',
        '''[[ "$PREFLIGHT_HOST" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,}$ ]] \\
  || die "Configure a valid FQDN or set HP_PANEL_HOST before installation"
PREFLIGHT_ADMIN_SOURCE="${HP_PANEL_ADMIN_CIDR:-}"
if [[ -z "$PREFLIGHT_ADMIN_SOURCE" && -n "${SSH_CLIENT:-}" ]]; then
  PREFLIGHT_ADMIN_SOURCE="${SSH_CLIENT%% *}"
fi
if [[ -z "$PREFLIGHT_ADMIN_SOURCE" && "${HP_ALLOW_PUBLIC_PANEL:-no}" != yes ]]; then
  die "Set HP_PANEL_ADMIN_CIDR, install over SSH, or explicitly set HP_ALLOW_PUBLIC_PANEL=yes"
fi
if [[ -n "$PREFLIGHT_ADMIN_SOURCE" ]]; then
  python3 - "$PREFLIGHT_ADMIN_SOURCE" <<'PYADMIN' \\
    || die "HP_PANEL_ADMIN_CIDR or SSH_CLIENT contains an invalid address"
import ipaddress
import sys
raw = sys.argv[1]
value = ipaddress.ip_network(raw, strict=False) if "/" in raw else ipaddress.ip_address(raw)
address = value.network_address if hasattr(value, "network_address") else value
if getattr(address, "scope_id", None):
    raise SystemExit("scoped IPv6 is unsupported")
PYADMIN
fi

if [[ "$REINSTALL" != yes && -f "$PANEL_DIR/config.env" ]]; then''',
        "early administrative source validation",
    )

    # A clean cloud image must receive every command used later by the installer.
    text = _replace_once(
        text,
        '''COMMON_PACKAGES=(openssl rsync acl gnupg sqlite3 needrestart inotify-tools smartmontools prometheus-node-exporter iproute2 git ca-certificates python3 python3-venv python3-pip curl ufw fail2ban unzip sudo nginx)''',
        '''COMMON_PACKAGES=(openssl rsync acl gnupg sqlite3 needrestart inotify-tools smartmontools prometheus-node-exporter iproute2 git ca-certificates python3 python3-venv python3-pip curl ufw fail2ban unzip sudo nginx openssh-server cron tar gzip util-linux hostname)''',
        "fresh-host command prerequisites",
    )
    text = _replace_once(
        text,
        '''    needrestart)            printf 'dnf-utils' ;;
    inotify-tools)          printf 'inotify-tools' ;;''',
        '''    needrestart)            printf 'dnf-utils' ;;
    cron)                   printf 'cronie' ;;
    inotify-tools)          printf 'inotify-tools' ;;''',
        "RHEL cron package mapping",
    )

    # The Rspamd Redis password must not inherit a world-readable default mode.
    text = _replace_once(
        text,
        '''if id _rspamd >/dev/null 2>&1; then chown -R _rspamd:_rspamd /etc/rspamd/local.d; elif id rspamd >/dev/null 2>&1; then chown -R rspamd:rspamd /etc/rspamd/local.d; fi

systemctl enable --now "$(svc redis)"''',
        '''if id _rspamd >/dev/null 2>&1; then chown -R _rspamd:_rspamd /etc/rspamd/local.d; elif id rspamd >/dev/null 2>&1; then chown -R rspamd:rspamd /etc/rspamd/local.d; fi
find /etc/rspamd/local.d -type d -exec chmod 750 {} +
find /etc/rspamd/local.d -type f -exec chmod 640 {} +

systemctl enable --now "$(svc redis)"''',
        "Rspamd secret permissions",
    )
    return text


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: harden_install.py SOURCE DESTINATION")
    source = pathlib.Path(sys.argv[1])
    destination = pathlib.Path(sys.argv[2])
    if not source.is_file() or source.is_symlink():
        raise SystemExit(f"unsafe installer base: {source}")
    transformed = MODULE.harden(source.read_text(encoding="utf-8"))
    transformed = compatibility_hardening(transformed)
    destination.write_text(transformed, encoding="utf-8")
    destination.chmod(0o700)


if __name__ == "__main__":
    main()
