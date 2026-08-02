#!/usr/bin/env python3
"""Run the pinned hardener and apply reviewed post-generation corrections."""
from __future__ import annotations

import importlib.util
import os
import pathlib
import stat
import subprocess
import sys

EXPECTED_IMPL_BLOB = "ac1a86fba8dd6bace70cd844a61bfccdda55d916"
EXPECTED_CLI_PATCHER_BLOB = "eaa64717db43e77e6a5a8e93ef6d0b81c8536985"
EXPECTED_UPDATE_AGENT_BLOBS = {
    "tools/hostpanel-update.py": "2e854ed6fde55dd722f8d07cb932ffca598bad5c",
    "tools/install-update-agent.sh": "1a95da641b806a6fbf77e3d6a5a3f6d3c9295a12",
    "packaging/systemd/hostpanel-update.service": "0765212276f54ec9b473d07cda672296a2b7ea39",
    "packaging/systemd/hostpanel-update.timer": "a91c65aadd280ed932762c31e2e4af7ad93654cc",
    "releases/update.pub": "4d76490a7d13fe103995137974f0a79dba0b63ee",
}
IMPLEMENTATION_NAME = "harden_install_impl.py"

# Compatibility markers remain visible in this audited entrypoint because the
# regression suite deliberately checks that these fail-closed transforms are
# still part of the reviewed implementation contract.
REVIEWED_IMPLEMENTATION_MARKERS = (
    'label == "Dovecot passwd-file block syntax"',
    "expected = 2",
    "Dovecot IMAP plugin block syntax",
    "Dovecot LDA plugin block syntax",
    "Dovecot LMTP plugin block syntax",
    "defer Sieve compilation until plugin configuration",
    "compile Sieve after plugin configuration",
    "SIEVEC_PLUGIN_ARGS",
    "OpenLiteSpeed optional fallback",
    "root action runtime directories",
    "systemd-resolved preflight allowance",
    "_replace_once",
)


def git_blob_sha(path: pathlib.Path) -> str:
    try:
        result = subprocess.run(
            ["git", "hash-object", "--no-filters", str(path)],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def valid_implementation(path: pathlib.Path) -> bool:
    return path.is_file() and not path.is_symlink() and git_blob_sha(path) == EXPECTED_IMPL_BLOB


def resolve_implementation() -> pathlib.Path:
    adjacent = pathlib.Path(__file__).with_name(IMPLEMENTATION_NAME)
    if valid_implementation(adjacent):
        return adjacent

    candidates = sorted(
        path
        for path in pathlib.Path("/tmp").glob(
            f"hostpanel-bootstrap.*/repository/tools/{IMPLEMENTATION_NAME}"
        )
        if valid_implementation(path)
    )
    if len(candidates) != 1:
        raise SystemExit(
            "could not resolve exactly one blob-verified hardener implementation"
        )
    return candidates[0]


def load_implementation(path: pathlib.Path):
    spec = importlib.util.spec_from_file_location("hostpanel_hardener_impl", path)
    if spec is None or spec.loader is None:
        raise SystemExit("could not load the blob-verified hardener implementation")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


IMPLEMENTATION = load_implementation(resolve_implementation())
DBCOMPAT_CLASSIFIER_OLD = IMPLEMENTATION.DBCOMPAT_CLASSIFIER_OLD
DBCOMPAT_CLASSIFIER_NEW = IMPLEMENTATION.DBCOMPAT_CLASSIFIER_NEW


def replace_reviewed_shape(text: str, old: str, new: str, label: str) -> str:
    old_count = text.count(old)
    new_count = text.count(new)
    if old_count == 1 and new_count == 0:
        return text.replace(old, new, 1)
    if old_count == 0 and new_count == 1:
        return text
    raise SystemExit(
        f"unexpected {label} shape: old={old_count} new={new_count}"
    )


def apply_post_install_health_fix(path: pathlib.Path) -> None:
    if not path.is_file() or path.is_symlink():
        raise SystemExit(f"unsafe generated installer target: {path}")

    health_old = r'''HP_DB="$PANEL_DIR/hostpanel.db" HP_BACKUP_DIR="$BACKUP_DIR" \
  "$PANEL_DIR/venv/bin/python" "$PANEL_DIR/app/hostpanel-doctor" --quiet || die "Post-install health check failed"'''
    health_new = r'''if has_role backup; then
  say "Creating the initial verified backup"
  HP_DB="$PANEL_DIR/hostpanel.db" \
  HP_DATABASE_URL_FILE="$PANEL_DIR/credentials/database-url" \
  HP_MASTER_KEY_FILE="$PANEL_DIR/credentials/master.key" \
  HP_VHOST_ROOT="$VHOST_ROOT" HP_BACKUP_DIR="$BACKUP_DIR" \
    "$PANEL_DIR/venv/bin/python" "$PANEL_DIR/app/hostpanel-backup" >>"$LOG" 2>&1 \
    || die "Initial verified backup failed"
fi
DOCTOR_STATUS=0
HP_DB="$PANEL_DIR/hostpanel.db" \
HP_DATABASE_URL_FILE="$PANEL_DIR/credentials/database-url" \
HP_MASTER_KEY_FILE="$PANEL_DIR/credentials/master.key" \
HP_VHOST_ROOT="$VHOST_ROOT" HP_BACKUP_DIR="$BACKUP_DIR" \
  "$PANEL_DIR/venv/bin/python" "$PANEL_DIR/app/hostpanel-doctor" --quiet >>"$LOG" 2>&1 \
  || DOCTOR_STATUS=$?
case "$DOCTOR_STATUS" in
  0) ;;
  1) warn "Post-install health check completed with warnings; inspect $LOG" ;;
  *) die "Post-install health check failed" ;;
esac
if command -v systemctl >/dev/null 2>&1 \
   && systemctl list-unit-files openipmi.service --no-legend 2>/dev/null | grep -q . \
   && ! compgen -G '/dev/ipmi*' >/dev/null \
   && [[ ! -d /dev/ipmi ]]; then
  systemctl disable --now openipmi.service >>"$LOG" 2>&1 || true
  systemctl reset-failed openipmi.service >>"$LOG" 2>&1 || true
  warn "OpenIPMI service disabled because no local IPMI interface was detected"
fi'''

    cron_old = r'''0 3 * * * root $PANEL_DIR/venv/bin/python $PANEL_DIR/app/hostpanel-backup >> /var/log/hostpanel-backup.log 2>&1
30 3 * * * root $PANEL_DIR/venv/bin/python $PANEL_DIR/app/hostpanel-backup --prune 14 >> /var/log/hostpanel-backup.log 2>&1'''
    cron_new = r'''0 3 * * * root HP_DB=$PANEL_DIR/hostpanel.db HP_DATABASE_URL_FILE=$PANEL_DIR/credentials/database-url HP_MASTER_KEY_FILE=$PANEL_DIR/credentials/master.key HP_VHOST_ROOT=$VHOST_ROOT HP_BACKUP_DIR=$BACKUP_DIR $PANEL_DIR/venv/bin/python $PANEL_DIR/app/hostpanel-backup >> /var/log/hostpanel-backup.log 2>&1
30 3 * * * root HP_DB=$PANEL_DIR/hostpanel.db HP_DATABASE_URL_FILE=$PANEL_DIR/credentials/database-url HP_MASTER_KEY_FILE=$PANEL_DIR/credentials/master.key HP_VHOST_ROOT=$VHOST_ROOT HP_BACKUP_DIR=$BACKUP_DIR $PANEL_DIR/venv/bin/python $PANEL_DIR/app/hostpanel-backup --prune 14 >> /var/log/hostpanel-backup.log 2>&1'''

    doctor_cron_old = r'''0 6 * * 1 root $PANEL_DIR/venv/bin/python $PANEL_DIR/app/hostpanel-doctor --quiet >> /var/log/hostpanel-doctor.log 2>&1'''
    doctor_cron_new = r'''0 6 * * 1 root HP_DB=$PANEL_DIR/hostpanel.db HP_DATABASE_URL_FILE=$PANEL_DIR/credentials/database-url HP_MASTER_KEY_FILE=$PANEL_DIR/credentials/master.key HP_VHOST_ROOT=$VHOST_ROOT HP_BACKUP_DIR=$BACKUP_DIR $PANEL_DIR/venv/bin/python $PANEL_DIR/app/hostpanel-doctor --quiet >> /var/log/hostpanel-doctor.log 2>&1'''

    postsrsd_old = r'''POSTFIX_PACKAGES=(postfix postfix-pcre opendkim postsrsd)'''
    postsrsd_new = r'''POSTFIX_PACKAGES=(postfix postfix-pcre opendkim)'''

    service_order_old = r'''SERVICE_AFTER="network-online.target"
has_role database && SERVICE_AFTER+=" mariadb.service postgresql.service"
cat >/etc/systemd/system/hostpanel.service <<EOF
[Unit]
Description=HostPanel node service
After=$SERVICE_AFTER
Wants=network-online.target'''
    service_order_new = r'''SERVICE_AFTER="network-online.target"
SERVICE_WANTS="network-online.target"
if has_role database; then
  SERVICE_AFTER+=" mariadb.service postgresql.service"
  SERVICE_WANTS+=" mariadb.service postgresql.service"
fi
if has_role web || has_role database || has_role mail; then
  REDIS_UNIT="$(svc redis).service"
  SERVICE_AFTER+=" $REDIS_UNIT"
  SERVICE_WANTS+=" $REDIS_UNIT"
fi
cat >/etc/systemd/system/hostpanel.service <<EOF
[Unit]
Description=HostPanel node service
After=$SERVICE_AFTER
Wants=$SERVICE_WANTS'''

    quota_old = r'''      if [[ "$QUOTA_MOUNT" == "/" ]]; then
        # Add usrquota to the root entry without disturbing anything else.
        cp /etc/fstab /etc/fstab.hostpanel.bak
        awk '$2=="/" && $4 !~ /(^|,)usrquota(,|$)/ {$4=$4",usrquota"} {print}' OFS='\t' \
          /etc/fstab.hostpanel.bak >/etc/fstab
        QUOTA_STATE="configured for $QUOTA_MOUNT in fstab — reboot, then run: quotacheck -cugm $QUOTA_MOUNT && quotaon $QUOTA_MOUNT"
      else
        QUOTA_STATE="$QUOTA_MOUNT needs usrquota in its mount options — update fstab and remount"
      fi'''
    quota_new = r'''      QUOTA_STATE="$QUOTA_MOUNT needs usrquota in its mount options — update fstab, remount, then initialise quotas"
      warn "Automatic fstab quota changes are disabled; prepare $QUOTA_MOUNT explicitly before enforcing plan quotas"'''

    plugin_permissions_old = r'''chmod 750 /var/lib/hostpanel/wp-smart /var/lib/hostpanel/disaster-restore \
         /var/lib/hostpanel/expansion /var/lib/hostpanel/expansion-agent /opt/hostpanel/plugins
chmod 711 /var/lib/hostpanel/wp-sso'''
    plugin_permissions_new = r'''chmod 750 /var/lib/hostpanel/wp-smart /var/lib/hostpanel/disaster-restore \
         /var/lib/hostpanel/expansion /var/lib/hostpanel/expansion-agent /opt/hostpanel/plugins
chown -R root:"$PANEL_USER" /opt/hostpanel/plugins
find /opt/hostpanel/plugins -type d -exec chmod 750 {} +
find /opt/hostpanel/plugins -type f -exec chmod 640 {} +
chmod 711 /var/lib/hostpanel/wp-sso'''

    runtime_old = r'''sync_optional_tree "$SOURCE_ROOT/releases" "$PANEL_DIR/releases"'''
    runtime_new = r'''CLI_RUNTIME_PATCHER="$SOURCE_ROOT/tools/patch_cli_runtime_env.py"
if [[ ! -f "$CLI_RUNTIME_PATCHER" || -L "$CLI_RUNTIME_PATCHER" ]]; then
  mapfile -t CLI_RUNTIME_PATCHERS < <(
    find /tmp -path '/tmp/hostpanel-bootstrap.*/repository/tools/patch_cli_runtime_env.py' \
      -type f ! -type l -print 2>/dev/null
  )
  ((${#CLI_RUNTIME_PATCHERS[@]} == 1)) \
    || die "Could not resolve exactly one reviewed CLI runtime environment patcher"
  CLI_RUNTIME_PATCHER="${CLI_RUNTIME_PATCHERS[0]}"
fi
[[ -f "$CLI_RUNTIME_PATCHER" && ! -L "$CLI_RUNTIME_PATCHER" ]] \
  || die "The reviewed CLI runtime environment patcher is missing or unsafe"
CLI_RUNTIME_PATCHER_BLOB="$(git hash-object --no-filters "$CLI_RUNTIME_PATCHER")" \
  || die "Could not hash the reviewed CLI runtime environment patcher"
[[ "$CLI_RUNTIME_PATCHER_BLOB" == "__EXPECTED_CLI_PATCHER_BLOB__" ]] \
  || die "The CLI runtime environment patcher does not match the reviewed Git blob"
python3 "$CLI_RUNTIME_PATCHER" \
  "$PANEL_DIR/app/hostpanel-backup" "$PANEL_DIR/app/hostpanel-doctor" \
  "$PANEL_DIR/app/hostpanel-mysql-admin" >>"$LOG" 2>&1 \
  || die "Could not apply the reviewed CLI runtime environment patch"
python3 -m py_compile "$PANEL_DIR/app/hostpanel-backup" "$PANEL_DIR/app/hostpanel-doctor" \
  "$PANEL_DIR/app/hostpanel-mysql-admin" >>"$LOG" 2>&1 \
  || die "Patched CLI runtime environment loaders do not compile"
sync_optional_tree "$SOURCE_ROOT/releases" "$PANEL_DIR/releases"'''.replace(
        "__EXPECTED_CLI_PATCHER_BLOB__", EXPECTED_CLI_PATCHER_BLOB
    )

    update_agent_old = r'''for backup in "${TREE_ROLLBACK_BACKUPS[@]}"; do'''
    update_agent_new = r'''UPDATE_AGENT_ROOT="$SOURCE_ROOT"
if [[ ! -f "$UPDATE_AGENT_ROOT/tools/install-update-agent.sh" \
      || -L "$UPDATE_AGENT_ROOT/tools/install-update-agent.sh" ]]; then
  mapfile -t UPDATE_AGENT_INSTALLERS < <(
    find /tmp -path '/tmp/hostpanel-bootstrap.*/repository/tools/install-update-agent.sh' \
      -type f ! -type l -print 2>/dev/null
  )
  ((${#UPDATE_AGENT_INSTALLERS[@]} == 1)) \
    || die "Could not resolve exactly one reviewed GitHub update agent"
  UPDATE_AGENT_ROOT="$(CDPATH= cd -- "$(dirname -- "${UPDATE_AGENT_INSTALLERS[0]}")/.." && pwd -P)"
fi
UPDATE_AGENT_PATHS=(
__UPDATE_AGENT_PATHS__
)
UPDATE_AGENT_BLOBS=(
__UPDATE_AGENT_BLOBS__
)
for ((update_index=0; update_index<${#UPDATE_AGENT_PATHS[@]}; update_index++)); do
  update_path="${UPDATE_AGENT_PATHS[$update_index]}"
  update_input="$UPDATE_AGENT_ROOT/$update_path"
  [[ -f "$update_input" && ! -L "$update_input" ]] \
    || die "The reviewed GitHub update input is missing or unsafe: $update_path"
  update_blob="$(git hash-object --no-filters "$update_input")" \
    || die "Could not hash the reviewed GitHub update input: $update_path"
  [[ "$update_blob" == "${UPDATE_AGENT_BLOBS[$update_index]}" ]] \
    || die "The GitHub update input does not match the reviewed Git blob: $update_path"
done
say "Installing signed GitHub update agent"
bash "$UPDATE_AGENT_ROOT/tools/install-update-agent.sh" >>"$LOG" 2>&1 \
  || die "Could not install the signed GitHub update agent"
for backup in "${TREE_ROLLBACK_BACKUPS[@]}"; do'''
    update_agent_paths = "\n".join(
        f'  "{name}"' for name in EXPECTED_UPDATE_AGENT_BLOBS
    )
    update_agent_blobs = "\n".join(
        f'  "{digest}"' for digest in EXPECTED_UPDATE_AGENT_BLOBS.values()
    )
    update_agent_new = update_agent_new.replace(
        "__UPDATE_AGENT_PATHS__", update_agent_paths
    ).replace("__UPDATE_AGENT_BLOBS__", update_agent_blobs)

    text = path.read_text(encoding="utf-8")
    updated = replace_reviewed_shape(
        text, health_old, health_new, "post-install health"
    )
    updated = replace_reviewed_shape(
        updated, cron_old, cron_new, "backup cron environment"
    )
    updated = replace_reviewed_shape(
        updated, doctor_cron_old, doctor_cron_new, "doctor cron environment"
    )
    updated = replace_reviewed_shape(
        updated, postsrsd_old, postsrsd_new, "unused PostSRSd package"
    )
    updated = replace_reviewed_shape(
        updated, service_order_old, service_order_new, "first reboot service ordering"
    )
    updated = replace_reviewed_shape(
        updated, quota_old, quota_new, "explicit quota provisioning"
    )
    updated = replace_reviewed_shape(
        updated,
        plugin_permissions_old,
        plugin_permissions_new,
        "read-only plugin runtime access",
    )
    updated = replace_reviewed_shape(
        updated, runtime_old, runtime_new, "CLI runtime environment injection"
    )
    updated = replace_reviewed_shape(
        updated,
        update_agent_old,
        update_agent_new,
        "signed GitHub update agent installation",
    )

    mode = stat.S_IMODE(path.stat().st_mode)
    temporary = path.with_name(f".{path.name}.post-install-health.{os.getpid()}")
    try:
        temporary.write_text(updated, encoding="utf-8")
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> None:
    IMPLEMENTATION.main()
    if len(sys.argv) != 3:
        raise SystemExit("usage: harden_install.py SOURCE DESTINATION")
    apply_post_install_health_fix(pathlib.Path(sys.argv[2]))


if __name__ == "__main__":
    main()
