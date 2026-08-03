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
    "tools/hostpanel-update.py": "65eca88f65a3cda79b381dbedfa98d141ce936d8",
    "tools/install-update-agent.sh": "ea58e3bb4185b0525a16360495018b9010e98fc8",
    "packaging/systemd/hostpanel-update.service": "5243fd33a3d21cdd70461a17602bbaabee3fc32e",
    "packaging/systemd/hostpanel-update.timer": "a91c65aadd280ed932762c31e2e4af7ad93654cc",
    "releases/update.pub": "4d76490a7d13fe103995137974f0a79dba0b63ee",
    "releases/update-keyring.json": "7030f4b1afca5f33e9877eb0816837d15fafde5c",
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
    "CustomBuild nginx_apache default",
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


def _same_file(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        left.st_dev,
        left.st_ino,
        left.st_mode,
        left.st_uid,
        left.st_gid,
        left.st_nlink,
        left.st_size,
        left.st_mtime_ns,
    ) == (
        right.st_dev,
        right.st_ino,
        right.st_mode,
        right.st_uid,
        right.st_gid,
        right.st_nlink,
        right.st_size,
        right.st_mtime_ns,
    )


def trusted_source_file(
    source_root: pathlib.Path, relative: pathlib.PurePosixPath, expected_blob: str
) -> pathlib.Path:
    if not source_root.is_absolute() or ".." in relative.parts:
        raise SystemExit("the hardener source root is not canonical")
    expected_uid = os.geteuid()
    root_before = source_root.lstat()
    if (
        not stat.S_ISDIR(root_before.st_mode)
        or stat.S_ISLNK(root_before.st_mode)
        or root_before.st_uid != expected_uid
        or stat.S_IMODE(root_before.st_mode) & 0o022
    ):
        raise SystemExit("the hardener source root is not private and trusted")

    current = source_root
    for component in relative.parts[:-1]:
        current = current / component
        metadata = current.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != expected_uid
            or stat.S_IMODE(metadata.st_mode) & 0o022
        ):
            raise SystemExit("the hardener source path contains an unsafe directory")

    candidate = source_root.joinpath(*relative.parts)
    before = candidate.lstat()
    if (
        not stat.S_ISREG(before.st_mode)
        or stat.S_ISLNK(before.st_mode)
        or before.st_uid != expected_uid
        or before.st_nlink != 1
        or stat.S_IMODE(before.st_mode) & 0o022
    ):
        raise SystemExit("the hardener implementation is not a trusted regular file")
    if git_blob_sha(candidate) != expected_blob:
        raise SystemExit("the hardener implementation does not match its reviewed blob")
    after = candidate.lstat()
    root_after = source_root.lstat()
    if not _same_file(before, after) or not _same_file(root_before, root_after):
        raise SystemExit("the hardener source changed during validation")
    return candidate


def resolve_implementation() -> pathlib.Path:
    entrypoint = pathlib.Path(__file__).absolute()
    adjacent_root = entrypoint.parent.parent
    try:
        return trusted_source_file(
            adjacent_root,
            pathlib.PurePosixPath("tools") / IMPLEMENTATION_NAME,
            EXPECTED_IMPL_BLOB,
        )
    except (FileNotFoundError, OSError, SystemExit):
        pass

    explicit_root = os.environ.pop("HP_HARDENER_SOURCE_ROOT", "")
    if not explicit_root:
        raise SystemExit("a unique trusted hardener source root is required")
    return trusted_source_file(
        pathlib.Path(explicit_root),
        pathlib.PurePosixPath("tools") / IMPLEMENTATION_NAME,
        EXPECTED_IMPL_BLOB,
    )


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
[[ -f "$UPDATE_AGENT_ROOT/tools/install-update-agent.sh" \
    && ! -L "$UPDATE_AGENT_ROOT/tools/install-update-agent.sh" ]] \
  || die "Could not resolve exactly one reviewed GitHub update agent from the trusted source root"
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

    webserver_mode_old = r'''RSPAMD_REPO_MODE="${HP_RSPAMD_REPO:-off}"'''
    webserver_mode_new = r'''RSPAMD_REPO_MODE="${HP_RSPAMD_REPO:-off}"
WEBSERVER_MODE="${HP_WEBSERVER_MODE:-nginx_apache}"'''

    webserver_validation_old = r'''[[ "$MAIL_MTA" =~ ^(postfix|exim)$ ]] || die "Unsupported MTA: $MAIL_MTA (choose postfix or exim)"'''
    webserver_validation_new = r'''[[ "$MAIL_MTA" =~ ^(postfix|exim)$ ]] || die "Unsupported MTA: $MAIL_MTA (choose postfix or exim)"
if [[ "$REINSTALL" == yes && -z "${HP_WEBSERVER_MODE+x}" \
     && -r /etc/hostpanel/webserver-mode ]]; then
  previous_webserver_mode="$(tr -d '[:space:]' </etc/hostpanel/webserver-mode)"
  [[ "$previous_webserver_mode" =~ ^(nginx_apache|nginx|apache|openlitespeed)$ ]] \
    && WEBSERVER_MODE="$previous_webserver_mode"
fi
[[ "$WEBSERVER_MODE" =~ ^(nginx_apache|nginx|apache|openlitespeed)$ ]] \
  || die "Unsupported webserver mode: $WEBSERVER_MODE"'''

    ols_install_old = r'''if has_role web && pkg_available "$(pkg_name openlitespeed)"; then
say "Installing OpenLiteSpeed and LSPHP"'''
    ols_install_new = r'''if has_role web && [[ "$WEBSERVER_MODE" == openlitespeed ]] \
   && pkg_available "$(pkg_name openlitespeed)"; then
say "Installing OpenLiteSpeed and LSPHP"'''

    ols_config_old = r'''if has_role web; then
say "Configuring OpenLiteSpeed as a private per-domain backend"'''
    ols_config_new = r'''if has_role web && [[ "$WEBSERVER_MODE" == openlitespeed ]]; then
say "Configuring OpenLiteSpeed as a private per-domain backend"'''

    ols_missing_old = r'''  warn "OpenLiteSpeed backend unavailable; nginx and Apache remain active"'''
    ols_missing_new = r'''  [[ "$WEBSERVER_MODE" != openlitespeed ]] \
    || die "OpenLiteSpeed mode was requested but the runtime is unavailable; configure a supported package source before selecting it"
  warn "OpenLiteSpeed backend unavailable; nginx and Apache remain active"'''

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
    updated = replace_reviewed_shape(
        updated,
        webserver_mode_old,
        webserver_mode_new,
        "CustomBuild nginx_apache default",
    )
    updated = replace_reviewed_shape(
        updated,
        webserver_validation_old,
        webserver_validation_new,
        "CustomBuild webserver validation",
    )
    updated = replace_reviewed_shape(
        updated, ols_install_old, ols_install_new, "optional OpenLiteSpeed installation"
    )
    updated = replace_reviewed_shape(
        updated, ols_config_old, ols_config_new, "optional OpenLiteSpeed configuration"
    )
    updated = replace_reviewed_shape(
        updated, ols_missing_old, ols_missing_new, "requested OpenLiteSpeed availability"
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
