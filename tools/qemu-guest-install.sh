#!/usr/bin/env bash
# Runs inside the ephemeral QEMU guest. Detailed installer output and generated
# credentials remain in a root-only log that is never exported as CI evidence.
set -Eeuo pipefail
umask 077

for input in /tmp/guest.env /tmp/bootstrap-install.sh /tmp/validate-production-vm.sh; do
  [[ -f "$input" && ! -L "$input" ]] || {
    printf 'Unsafe or missing QEMU guest input: %s\n' "$input" >&2
    exit 1
  }
done
[[ "$(stat -c '%u:%a:%h' /tmp/guest.env)" == "0:600:1" ]] || {
  printf '%s\n' 'QEMU guest environment must be a root-owned 0600 regular file with one link.' >&2
  exit 1
}
install -o root -g root -m 600 /tmp/guest.env /root/hostpanel-qemu.env
source /root/hostpanel-qemu.env
EVIDENCE=/root/hostpanel-qemu-evidence
PREFLIGHT_LOG="$EVIDENCE/preflight.log"
PRIVATE_LOG=/root/hostpanel-qemu-private-install.log
FAILURE_PHASE=installation
install -d -o root -g root -m 700 "$EVIDENCE"
: > "$PRIVATE_LOG"
chmod 600 "$PRIVATE_LOG"

collect_failure_evidence(){
  local status=$? stage=""
  trap - ERR
  if [[ -r /etc/hostpanel/install-state ]]; then
    stage="$(awk -F= '$1 == "stage" {sub(/^stage=/, ""); print; exit}' /etc/hostpanel/install-state)"
  fi
  {
    printf 'exit_status=%s\n' "$status"
    printf 'failure_phase=%s\n' "$FAILURE_PHASE"
    printf 'expected_version=%s\n' "$HP_EXPECTED_VERSION"
    if [[ -r /opt/hostpanel/VERSION ]]; then
      printf 'installed_version=%s\n' "$(tr -d '[:space:]' </opt/hostpanel/VERSION)"
    fi
    printf '%s\n' '--- install state ---'
    if [[ -r /etc/hostpanel/install-state ]]; then
      cat /etc/hostpanel/install-state
    else
      printf '%s\n' 'install-state unavailable'
    fi
    printf '%s\n' '--- failed units after rollback ---'
    systemctl --failed --no-legend --plain 2>&1 || true
    printf '%s\n' '--- redacted installer errors ---'
    if [[ -r /var/log/hostpanel-install.log ]]; then
      grep -Eai '(^==>|error|failed|failure|fatal|denied|cannot|could not|invalid|not found|timed out|refused|rejected|syntax|openlitespeed|litespeed|lsws|unit .*failed)' \
        /var/log/hostpanel-install.log \
        | grep -Evai '(password|passwd|secret|token|credential|private[ _-]?key|api[ _-]?key|admin[ _-]?(user|login))' \
        | tail -n 280 || true
    else
      printf '%s\n' 'installer log unavailable'
    fi
    if [[ "$stage" == 'Configuring OpenLiteSpeed as a private per-domain backend' ]]; then
      printf '%s\n' '--- scoped pre-rollback OpenLiteSpeed errors ---'
      if [[ -r "$PRIVATE_LOG" ]]; then
        grep -Eai '(openlitespeed|litespeed|lsws|httpd_config|admin_config|traceback|systemexit|syntax|rejected|failed to start)' \
          "$PRIVATE_LOG" \
          | grep -Evai '(password|passwd|secret|token|credential|private[ _-]?key|api[ _-]?key|admin[ _-]?(user|login))' \
          | tail -n 180 || true
      else
        printf '%s\n' 'private outer installer log unavailable'
      fi
      printf '%s\n' '--- scoped post-rollback OpenLiteSpeed configuration test ---'
      if [[ -x /usr/local/lsws/bin/openlitespeed ]]; then
        /usr/local/lsws/bin/openlitespeed -t 2>&1 | tail -n 160 || true
      else
        printf '%s\n' 'OpenLiteSpeed binary unavailable after rollback'
      fi
      printf '%s\n' '--- scoped OpenLiteSpeed managed directives ---'
      grep -EHn '^[[:space:]]*(include|listener|address|secure|useIpInProxyHeader)([[:space:]]|$)' \
        /usr/local/lsws/conf/httpd_config.conf \
        /usr/local/lsws/conf/hostpanel/hostpanel.conf 2>/dev/null \
        | tail -n 120 || true
    fi
    if [[ "$stage" == 'Registering the systemd service' ]]; then
      printf '%s\n' '--- scoped hostpanel service state ---'
      systemctl show hostpanel.service \
        --property=LoadState,ActiveState,SubState,Result,ExecMainCode,ExecMainStatus,FragmentPath \
        --no-pager 2>&1 || true
      printf '%s\n' '--- scoped redacted hostpanel service errors ---'
      {
        if [[ -r "$PRIVATE_LOG" ]]; then
          grep -Eai '(hostpanel\.service|uvicorn|traceback|exception|error|failed|permission denied|no such file|module.*not found|cannot|could not)' \
            "$PRIVATE_LOG" || true
        fi
        journalctl -u hostpanel.service --no-pager -n 240 2>&1 || true
      } \
        | grep -Evai '(password|passwd|secret|token|credential|private[ _-]?key|api[ _-]?key|admin[ _-]?(user|login))' \
        | sed -E 's#((postgres(ql)?|redis|mysql|mariadb)://[^:/[:space:]]+:)[^@[:space:]]+@#\1[REDACTED]@#g' \
        | tail -n 240 || true
    fi
    if [[ "$stage" == 'Granting the panel controlled root actions' ]]; then
      printf '%s\n' '--- scoped pre-rollback root-action errors ---'
      if [[ -r "$PRIVATE_LOG" ]]; then
        grep -Eai '(visudo|sudoers|chown:|chmod:|find:|cannot access|no such file|privileged file listed|root actions|/venvs([ /]|$))' \
          "$PRIVATE_LOG" \
          | grep -Evai '(password|passwd|secret|token|credential|private[ _-]?key|api[ _-]?key|admin[ _-]?(user|login))' \
          | tail -n 180 || true
      else
        printf '%s\n' 'private outer installer log unavailable'
      fi
      printf '%s\n' '--- scoped post-rollback sudoers validation ---'
      if [[ -r /etc/sudoers.d/hostpanel ]]; then
        visudo -cf /etc/sudoers.d/hostpanel 2>&1 || true
      else
        printf '%s\n' 'HostPanel sudoers file unavailable after rollback'
      fi
    fi
  } > "$EVIDENCE/install-failure-summary.txt"
  chmod 600 "$EVIDENCE/install-failure-summary.txt"
  printf 'Guest %s failed; exported stage and redacted error evidence only.\n' "$FAILURE_PHASE" >&2
  exit "$status"
}
trap collect_failure_evidence ERR

test "$(cat /proc/1/comm)" = systemd
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq >> "$PRIVATE_LOG" 2>&1
apt-get install -y -qq ca-certificates curl git openssl python3 >> "$PRIVATE_LOG" 2>&1
install -o root -g root -m 700 /tmp/bootstrap-install.sh /root/bootstrap-install.sh
install -o root -g root -m 700 /tmp/validate-production-vm.sh /root/validate-production-vm.sh

cat > /root/hostpanel-qemu-post-reboot.sh <<'POSTREBOOT'
#!/usr/bin/env bash
set -Eeuo pipefail
source /root/hostpanel-qemu.env
EVIDENCE=/root/hostpanel-qemu-evidence
STABLE_CHECKS=0
for attempt in $(seq 1 45); do
  if systemctl is-active --quiet hostpanel.service \
     && ss -lntH 2>/dev/null | awk '$4 ~ /:12722$/ {found=1} END {exit !found}'; then
    STABLE_CHECKS=$((STABLE_CHECKS + 1))
    ((STABLE_CHECKS >= 5)) && break
  else
    STABLE_CHECKS=0
  fi
  sleep 2
done
if ((STABLE_CHECKS < 5)); then
  {
    printf '%s\n' '--- scoped hostpanel service state after reboot ---'
    systemctl show hostpanel.service \
      --property=LoadState,ActiveState,SubState,Result,ExecMainCode,ExecMainStatus,FragmentPath,NRestarts \
      --no-pager 2>&1 || true
    printf '%s\n' '--- scoped redacted hostpanel service journal after reboot ---'
    journalctl -u hostpanel.service --no-pager -n 240 2>&1 \
      | grep -Evai '(password|passwd|secret|token|credential|private[ _-]?key|api[ _-]?key|admin[ _-]?(user|login))' \
      | sed -E 's#((postgres(ql)?|redis|mysql|mariadb)://[^:/[:space:]]+:)[^@[:space:]]+@#\1[REDACTED]@#g' \
      | tail -n 240 || true
  } > "$EVIDENCE/hostpanel-post-reboot-failure.txt"
  chmod 600 "$EVIDENCE/hostpanel-post-reboot-failure.txt"
  exit 1
fi
env \
  HP_EXPECTED_VERSION="$HP_EXPECTED_VERSION" \
  HP_PANEL_HOST="$HP_PANEL_HOST" \
  HP_EXPECTED_PUBLIC_IP="$HP_EXPECTED_PUBLIC_IP" \
  bash /root/validate-production-vm.sh --post-reboot \
  | tee "$EVIDENCE/post-reboot-validator.txt"
set +e
/opt/hostpanel/venv/bin/python /opt/hostpanel/app/hostpanel-doctor --quiet \
  | tee "$EVIDENCE/doctor.txt"
DOCTOR_STATUS=${PIPESTATUS[0]}
set -e
case "$DOCTOR_STATUS" in
  0|1) ;;
  *) exit "$DOCTOR_STATUS" ;;
esac
POSTREBOOT
chmod 700 /root/hostpanel-qemu-post-reboot.sh

common_env=(
  HP_REPO_REF="$HP_REVIEWED_COMMIT_SHA"
  HP_PANEL_HOST="$HP_PANEL_HOST"
  HP_PANEL_ADMIN_CIDR="$HP_PANEL_ADMIN_CIDR"
  HP_MULTI_PHP_REPO=off
  HP_RSPAMD_REPO=off
)
install_args=(--mta "$HP_MTA")
echo 'Running installer preflight; its non-secret diagnostics are exported as evidence.'
if ! env "${common_env[@]}" bash /root/bootstrap-install.sh --check "${install_args[@]}" > "$PREFLIGHT_LOG" 2>&1; then
  echo 'Installer preflight failed:' >&2
  tail -n 200 "$PREFLIGHT_LOG" >&2
  exit 1
fi

echo 'Running full installation; generated credentials stay in the root-only guest log.'
env "${common_env[@]}" bash /root/bootstrap-install.sh "${install_args[@]}" >> "$PRIVATE_LOG" 2>&1

FAILURE_PHASE=pre-reboot-validation
ACTUAL_VERSION="$(tr -d '[:space:]' < /opt/hostpanel/VERSION)"
if [[ "$ACTUAL_VERSION" != "$HP_EXPECTED_VERSION" ]]; then
  printf 'Installed version mismatch: expected %s, got %s\n' \
    "$HP_EXPECTED_VERSION" "$ACTUAL_VERSION" >&2
  false
fi
env \
  HP_EXPECTED_VERSION="$HP_EXPECTED_VERSION" \
  HP_PANEL_HOST="$HP_PANEL_HOST" \
  HP_EXPECTED_PUBLIC_IP="$HP_EXPECTED_PUBLIC_IP" \
  bash /root/validate-production-vm.sh --check \
  | tee "$EVIDENCE/pre-reboot-validator.txt"
env \
  HP_EXPECTED_VERSION="$HP_EXPECTED_VERSION" \
  HP_PANEL_HOST="$HP_PANEL_HOST" \
  HP_EXPECTED_PUBLIC_IP="$HP_EXPECTED_PUBLIC_IP" \
  bash /root/validate-production-vm.sh --prepare-reboot \
  | tee "$EVIDENCE/prepare-reboot-validator.txt"
cat /etc/os-release > "$EVIDENCE/os-release.txt"
uname -a > "$EVIDENCE/uname.txt"
cat /proc/sys/kernel/random/boot_id > "$EVIDENCE/pre-reboot-boot-id.txt"
cat /opt/hostpanel/VERSION > "$EVIDENCE/version.txt"
systemctl --failed --no-legend --plain > "$EVIDENCE/failed-units-pre-reboot.txt" || true
