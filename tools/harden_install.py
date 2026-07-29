#!/usr/bin/env python3
"""Run the deterministic installer hardening transform with compatibility fixes."""
from __future__ import annotations

import importlib.util
import pathlib
import re
import sys

MODULE_PATH = pathlib.Path(__file__).with_name("harden_install_runtime.py")
SPEC = importlib.util.spec_from_file_location("hostpanel_hardener", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise SystemExit("could not load the installer hardening implementation")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

_original_module_replace_once = MODULE.replace_once
_original_regex_once = MODULE.regex_once

DBCOMPAT_CLASSIFIER_OLD = r'''        for statement in statements:
            match = re.match(r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+([a-zA-Z_][a-zA-Z0-9_]*)", statement, re.I)
            (tables if match else others).append((match.group(1).lower(), statement) if match else statement)'''

DBCOMPAT_CLASSIFIER_NEW = r'''        for statement in statements:
            classified = re.sub(r"--[^\n]*(?:\n|$)", "\n", statement)
            classified = re.sub(r"/\*.*?\*/", " ", classified, flags=re.S)
            match = re.search(
                r"\bCREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+([a-zA-Z_][a-zA-Z0-9_]*)",
                classified,
                re.I,
            )
            (tables if match else others).append((match.group(1).lower(), statement) if match else statement)'''


def _module_replace_once(text: str, old: str, new: str, label: str) -> str:
    if label == "rollback state":
        count = text.count(old)
        if count < 1:
            raise SystemExit(f"{label}: expected at least one match, found {count}")
        return text.replace(old, new, 1)
    if label == "validate loaded PHP baseline":
        pattern = re.compile(
            r'''printf '%s\\n' "\$\{PHP_INSTALLED\[@\]\}" >/etc/hostpanel/php-versions\n'''
            r'''\s*ok "PHP-FPM installed: \$\{PHP_INSTALLED\[\*\]\}"'''
        )
        updated, count = pattern.subn(lambda _: new, text, count=1)
        if count != 1:
            raise SystemExit(f"{label}: expected exactly one structural match, found {count}")
        return updated
    return _original_module_replace_once(text, old, new, label)


def _replace_once(text: str, old: str, new: str, label: str) -> str:
    expected = 2 if label == "Dovecot passwd-file block syntax" else 1
    count = text.count(old)
    if count != expected:
        raise SystemExit(f"{label}: expected exactly {expected} match(es), found {count}")
    return text.replace(old, new, expected)


def _regex_once(text: str, pattern: str, replacement: str, label: str) -> str:
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

    text = _replace_once(
        text,
        r'''[[ "$PREFLIGHT_HOST" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,}$ ]] \
  || die "Configure a valid FQDN or set HP_PANEL_HOST before installation"

if [[ "$REINSTALL" != yes && -f "$PANEL_DIR/config.env" ]]; then''',
        r'''[[ "$PREFLIGHT_HOST" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,}$ ]] \
  || die "Configure a valid FQDN or set HP_PANEL_HOST before installation"
PREFLIGHT_ADMIN_SOURCE="${HP_PANEL_ADMIN_CIDR:-}"
if [[ -z "$PREFLIGHT_ADMIN_SOURCE" && -n "${SSH_CLIENT:-}" ]]; then
  PREFLIGHT_ADMIN_SOURCE="${SSH_CLIENT%% *}"
fi
if [[ -z "$PREFLIGHT_ADMIN_SOURCE" && "${HP_ALLOW_PUBLIC_PANEL:-no}" != yes ]]; then
  die "Set HP_PANEL_ADMIN_CIDR, install over SSH, or explicitly set HP_ALLOW_PUBLIC_PANEL=yes"
fi
if [[ -n "$PREFLIGHT_ADMIN_SOURCE" ]]; then
  python3 - "$PREFLIGHT_ADMIN_SOURCE" <<'PYADMIN' \
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

    text = _replace_once(
        text,
        '''  for port in "${PORTS[@]}"; do
    if ss -ltnup 2>/dev/null | grep -Eq "[:.]${port}[[:space:]]"; then
      die "Port $port is already in use. Use a clean server or set HP_ALLOW_EXISTING_STACK=yes after reviewing conflicts."
    fi
  done''',
        '''  for port in "${PORTS[@]}"; do
    LISTENERS="$(ss -H -ltnup 2>/dev/null | grep -E "[:.]${port}[[:space:]]" || true)"
    if [[ -n "$LISTENERS" ]]; then
      if [[ "$port" == 53 ]] \
         && ! grep -Fvq 'systemd-resolve' <<<"$LISTENERS" \
         && ! grep -Ev '127\\.0\\.0\\.(53|54)(%lo)?:53([[:space:]]|$)' <<<"$LISTENERS" >/dev/null; then
        warn "systemd-resolved loopback DNS stub will be disabled before the DNS role is installed"
        continue
      fi
      die "Port $port is already in use. Use a clean server or set HP_ALLOW_EXISTING_STACK=yes after reviewing conflicts."
    fi
  done''',
        "systemd-resolved preflight allowance",
    )

    text = _replace_once(
        text,
        '''# --------------------------------------------------------------------------- #
say "Installing packages for roles: $ROLE_CSV"''',
        '''# --------------------------------------------------------------------------- #
if has_role dns && [[ "$PKG_FAMILY" == debian ]] \
   && command -v systemctl >/dev/null 2>&1 \
   && systemctl is-active --quiet systemd-resolved 2>/dev/null \
   && ss -H -ltnup 2>/dev/null | grep -E '127\\.0\\.0\\.(53|54)(%lo)?:53([[:space:]]|$)' | grep -Fq 'systemd-resolve'; then
  say "Preparing systemd-resolved for the authoritative DNS role"
  install -d -o root -g root -m 755 /etc/systemd/resolved.conf.d
  cat >/etc/systemd/resolved.conf.d/hostpanel-dns.conf <<'EOFRESOLVED'
[Resolve]
DNSStubListener=no
EOFRESOLVED
  chmod 644 /etc/systemd/resolved.conf.d/hostpanel-dns.conf
  if [[ -e /run/systemd/resolve/resolv.conf ]]; then
    ln -sfn /run/systemd/resolve/resolv.conf /etc/resolv.conf
  fi
  systemctl restart systemd-resolved >>"$LOG" 2>&1 \
    || die "Could not restart systemd-resolved without its DNS stub"
  if ss -H -ltnup 2>/dev/null | grep -E '[:.]53[[:space:]]' | grep -Fq 'systemd-resolve'; then
    die "systemd-resolved still owns port 53 after disabling its DNS stub"
  fi
  ok "systemd-resolved upstream resolver retained without a port 53 listener"
fi

say "Installing packages for roles: $ROLE_CSV"''',
        "systemd-resolved DNS preparation",
    )

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

    text = _replace_once(
        text,
        '''passdb { driver = passwd-file; args = /etc/dovecot/users }
userdb { driver = passwd-file; args = /etc/dovecot/users }''',
        '''passdb {
  driver = passwd-file
  args = /etc/dovecot/users
}
userdb {
  driver = passwd-file
  args = /etc/dovecot/users
}''',
        "Dovecot passwd-file block syntax",
    )
    text = _replace_once(
        text,
        '''protocol imap { mail_plugins = $mail_plugins imap_sieve }''',
        '''protocol imap {
  mail_plugins = $mail_plugins imap_sieve
}''',
        "Dovecot IMAP plugin block syntax",
    )
    text = _replace_once(
        text,
        '''protocol lda { mail_plugins = $mail_plugins sieve }''',
        '''protocol lda {
  mail_plugins = $mail_plugins sieve
}''',
        "Dovecot LDA plugin block syntax",
    )
    text = _replace_once(
        text,
        '''protocol lmtp { mail_plugins = $mail_plugins sieve }''',
        '''protocol lmtp {
  mail_plugins = $mail_plugins sieve
}''',
        "Dovecot LMTP plugin block syntax",
    )

    text = _replace_once(
        text,
        '''chmod 755 /usr/lib/dovecot/sieve/hostpanel-rspamc-spam /usr/lib/dovecot/sieve/hostpanel-rspamc-ham
chmod 644 /usr/lib/dovecot/sieve/report-spam.sieve /usr/lib/dovecot/sieve/report-ham.sieve
/usr/bin/sievec /usr/lib/dovecot/sieve/report-spam.sieve
/usr/bin/sievec /usr/lib/dovecot/sieve/report-ham.sieve
cat >/etc/dovecot/conf.d/90-sieve-hostpanel.conf <<'EOF'
# Managed by HostPanel''',
        '''chmod 755 /usr/lib/dovecot/sieve/hostpanel-rspamc-spam /usr/lib/dovecot/sieve/hostpanel-rspamc-ham
chmod 644 /usr/lib/dovecot/sieve/report-spam.sieve /usr/lib/dovecot/sieve/report-ham.sieve
cat >/etc/dovecot/conf.d/90-sieve-hostpanel.conf <<'EOF'
# Managed by HostPanel''',
        "defer Sieve compilation until plugin configuration",
    )
    text = _replace_once(
        text,
        '''EOF
doveconf -n >>"$LOG" 2>&1 || die "Dovecot IMAPSieve configuration is invalid"''',
        '''EOF
doveconf -n >>"$LOG" 2>&1 || die "Dovecot IMAPSieve configuration is invalid"
SIEVEC_PLUGIN_ARGS=(
  -c /etc/dovecot/dovecot.conf
  -o 'sieve_plugins=sieve_imapsieve sieve_extprograms'
  -o 'sieve_global_extensions=+vnd.dovecot.pipe +vnd.dovecot.environment'
  -x '+imapsieve +vnd.dovecot.pipe'
)
/usr/bin/sievec "${SIEVEC_PLUGIN_ARGS[@]}" /usr/lib/dovecot/sieve/report-spam.sieve >>"$LOG" 2>&1 \
  || die "Could not compile the spam-learning Sieve rule"
/usr/bin/sievec "${SIEVEC_PLUGIN_ARGS[@]}" /usr/lib/dovecot/sieve/report-ham.sieve >>"$LOG" 2>&1 \
  || die "Could not compile the ham-learning Sieve rule"''',
        "compile Sieve after plugin configuration",
    )

    runtime_patch = f'''sync_release_tree "$SOURCE_ROOT/app" "$PANEL_DIR/app"
python3 - "$PANEL_DIR/app/dbcompat.py" <<'PYDBCOMPAT' >>"$LOG" 2>&1 \
  || die "Could not apply the reviewed PostgreSQL schema compatibility patch"
import os
import pathlib
import stat
import sys

path = pathlib.Path(sys.argv[1])
if not path.is_file() or path.is_symlink():
    raise SystemExit(f"unsafe dbcompat target: {{path}}")
old = {DBCOMPAT_CLASSIFIER_OLD!r}
new = {DBCOMPAT_CLASSIFIER_NEW!r}
text = path.read_text(encoding="utf-8")
old_count = text.count(old)
new_count = text.count(new)
if old_count == 1 and new_count == 0:
    updated = text.replace(old, new, 1)
elif old_count == 0 and new_count == 1:
    updated = text
else:
    raise SystemExit(
        f"unexpected dbcompat classifier shape: old={{old_count}} new={{new_count}}"
    )
mode = stat.S_IMODE(path.stat().st_mode)
temporary = path.with_name(f".{{path.name}}.hostpanel.{{os.getpid()}}")
try:
    temporary.write_text(updated, encoding="utf-8")
    os.chmod(temporary, mode)
    os.replace(temporary, path)
finally:
    temporary.unlink(missing_ok=True)
PYDBCOMPAT
python3 -m py_compile "$PANEL_DIR/app/dbcompat.py" >>"$LOG" 2>&1 \
  || die "Patched PostgreSQL compatibility module does not compile"
python3 - "$PANEL_DIR/app/store.py" "$PANEL_DIR/app/platform_store.py" "$PANEL_DIR/app/platform_worker.py" <<'PYRESERVEDSQL' >>"$LOG" 2>&1 \
  || die "Could not apply the reviewed reserved SQL identifier patch"
import os
import pathlib
import stat
import sys

patches = {{
    pathlib.Path(sys.argv[1]): (
        (
            "traffic cursor schema",
            "    offset       INTEGER NOT NULL DEFAULT 0,",
            "    cursor_offset INTEGER NOT NULL DEFAULT 0,",
            1,
        ),
    ),
    pathlib.Path(sys.argv[2]): (
        (
            "platform log cursor schema",
            "    offset      INTEGER NOT NULL DEFAULT 0,",
            "    cursor_offset INTEGER NOT NULL DEFAULT 0,",
            1,
        ),
    ),
    pathlib.Path(sys.argv[3]): (
        (
            "platform cursor select",
            "SELECT inode,offset FROM platform_log_cursors WHERE source=?",
            "SELECT inode,cursor_offset FROM platform_log_cursors WHERE source=?",
            1,
        ),
        (
            "platform cursor row access",
            'cursor["offset"]',
            'cursor["cursor_offset"]',
            2,
        ),
        (
            "platform cursor insert",
            "INSERT INTO platform_log_cursors(source,inode,offset,updated) VALUES(?,?,?,?) ",
            "INSERT INTO platform_log_cursors(source,inode,cursor_offset,updated) VALUES(?,?,?,?) ",
            1,
        ),
        (
            "platform cursor upsert",
            "ON CONFLICT(source) DO UPDATE SET inode=excluded.inode,offset=excluded.offset,updated=excluded.updated",
            "ON CONFLICT(source) DO UPDATE SET inode=excluded.inode,cursor_offset=excluded.cursor_offset,updated=excluded.updated",
            1,
        ),
    ),
}}

for path, replacements in patches.items():
    if not path.is_file() or path.is_symlink():
        raise SystemExit(f"unsafe runtime patch target: {{path}}")
    text = path.read_text(encoding="utf-8")
    updated = text
    for label, old, new, expected in replacements:
        old_count = updated.count(old)
        new_count = updated.count(new)
        if old_count == expected and new_count == 0:
            updated = updated.replace(old, new, expected)
        elif old_count == 0 and new_count == expected:
            continue
        else:
            raise SystemExit(
                f"unexpected {{label}} shape in {{path.name}}: old={{old_count}} new={{new_count}}"
            )
    if updated == text:
        continue
    mode = stat.S_IMODE(path.stat().st_mode)
    temporary = path.with_name(f".{{path.name}}.hostpanel.{{os.getpid()}}")
    try:
        temporary.write_text(updated, encoding="utf-8")
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
PYRESERVEDSQL
python3 -m py_compile "$PANEL_DIR/app/store.py" "$PANEL_DIR/app/platform_store.py" "$PANEL_DIR/app/platform_worker.py" >>"$LOG" 2>&1 \
  || die "Patched SQL cursor modules do not compile"
sync_optional_tree "$SOURCE_ROOT/releases" "$PANEL_DIR/releases"'''
    text = _replace_once(
        text,
        '''sync_release_tree "$SOURCE_ROOT/app" "$PANEL_DIR/app"
sync_optional_tree "$SOURCE_ROOT/releases" "$PANEL_DIR/releases"''',
        runtime_patch,
        "PostgreSQL runtime compatibility patches",
    )

    text = _replace_once(
        text,
        '  die "OpenLiteSpeed binary is missing after installation"',
        '  warn "OpenLiteSpeed backend unavailable; nginx and Apache remain active"',
        "OpenLiteSpeed optional fallback",
    )

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
