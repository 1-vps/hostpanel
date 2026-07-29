#!/usr/bin/env bash
# Boot an ephemeral Ubuntu cloud image under QEMU, install HostPanel, reboot it,
# and run the production VM validator without external VPS credentials.
set -Eeuo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK_DIR="${RUNNER_TEMP:-/tmp}/hostpanel-qemu-acceptance"
ARTIFACT_DIR="$REPO_ROOT/artifacts/qemu-vm-acceptance"
IMAGE_URL="${HP_QEMU_IMAGE_URL:-https://cloud-images.ubuntu.com/releases/noble/release-20260725/ubuntu-24.04-server-cloudimg-amd64.img}"
IMAGE_SHA256="${HP_QEMU_IMAGE_SHA256:-d1940f7d69d343355e183dff1e08a59852d32e7309baa7a4bad8365b11b005ac}"
REVIEWED_COMMIT_SHA="${HP_QEMU_REVIEWED_COMMIT_SHA:-}"
EXPECTED_VERSION="${HP_QEMU_EXPECTED_VERSION:-3.4.0-hardened-r6}"
MTA="${HP_QEMU_MTA:-postfix}"
SSH_PORT="${HP_QEMU_SSH_PORT:-22022}"
PANEL_FORWARD_PORT="${HP_QEMU_PANEL_FORWARD_PORT:-32222}"
VM_MEMORY_MB="${HP_QEMU_MEMORY_MB:-8192}"
VM_CPUS="${HP_QEMU_CPUS:-4}"
VM_DISK_SIZE="${HP_QEMU_DISK_SIZE:-32G}"
PANEL_HOST="${HP_QEMU_PANEL_HOST:-panel.10.0.2.15.nip.io}"
GUEST_IP="${HP_QEMU_GUEST_IP:-10.0.2.15}"
ADMIN_CIDR="${HP_QEMU_ADMIN_CIDR:-10.0.2.2/32}"
QEMU_PID=""

mkdir -p "$WORK_DIR" "$ARTIFACT_DIR"
chmod 700 "$WORK_DIR"
rm -rf "$ARTIFACT_DIR"/*
exec > >(tee "$ARTIFACT_DIR/runner.log") 2>&1

die(){ printf 'Error: %s\n' "$*" >&2; exit 1; }
require(){ command -v "$1" >/dev/null 2>&1 || die "$1 is required"; }
ssh_opts=(
  -i "$WORK_DIR/id_ed25519"
  -p "$SSH_PORT"
  -o BatchMode=yes
  -o IdentitiesOnly=yes
  -o StrictHostKeyChecking=accept-new
  -o UserKnownHostsFile="$WORK_DIR/known_hosts"
  -o ConnectTimeout=10
  -o ServerAliveInterval=15
  -o ServerAliveCountMax=4
)
scp_opts=(
  -i "$WORK_DIR/id_ed25519"
  -P "$SSH_PORT"
  -o BatchMode=yes
  -o IdentitiesOnly=yes
  -o StrictHostKeyChecking=accept-new
  -o UserKnownHostsFile="$WORK_DIR/known_hosts"
  -o ConnectTimeout=10
)

collect_evidence(){
  set +e
  if [[ -n "$QEMU_PID" ]] && kill -0 "$QEMU_PID" 2>/dev/null; then
    ssh "${ssh_opts[@]}" hostpanel@127.0.0.1 \
      'sudo tar -C /root -czf - hostpanel-qemu-evidence 2>/dev/null' \
      > "$WORK_DIR/guest-evidence.tgz" 2>/dev/null
    if [[ -s "$WORK_DIR/guest-evidence.tgz" ]]; then
      tar -xzf "$WORK_DIR/guest-evidence.tgz" -C "$ARTIFACT_DIR" 2>/dev/null || true
    fi
    ssh "${ssh_opts[@]}" hostpanel@127.0.0.1 \
      'sudo systemctl --failed --no-legend --plain; sudo journalctl -b -p warning..alert --no-pager -n 300' \
      > "$ARTIFACT_DIR/guest-failure-diagnostics.txt" 2>&1 || true
  fi
  [[ ! -f "$WORK_DIR/console.log" ]] || cp "$WORK_DIR/console.log" "$ARTIFACT_DIR/qemu-console.log"
  df -h > "$ARTIFACT_DIR/runner-disk.txt" 2>&1 || true
  if [[ -n "$QEMU_PID" ]] && kill -0 "$QEMU_PID" 2>/dev/null; then
    kill "$QEMU_PID" 2>/dev/null || true
    for _ in {1..20}; do
      kill -0 "$QEMU_PID" 2>/dev/null || break
      sleep 1
    done
    kill -KILL "$QEMU_PID" 2>/dev/null || true
  fi
}
trap collect_evidence EXIT

for command in curl qemu-img qemu-system-x86_64 cloud-localds ssh scp ssh-keygen nc; do
  require "$command"
done
[[ "$REVIEWED_COMMIT_SHA" =~ ^[0-9a-fA-F]{40}$ ]] \
  || die 'HP_QEMU_REVIEWED_COMMIT_SHA must be a reviewed full 40-character commit SHA'
[[ "$EXPECTED_VERSION" =~ ^[0-9]+(\.[0-9]+){2}([-+][0-9A-Za-z][0-9A-Za-z.-]*)?$ ]] \
  || die 'HP_QEMU_EXPECTED_VERSION must be a release version'
case "$MTA" in postfix|exim) ;; *) die "unsupported MTA: $MTA" ;; esac
for port in "$SSH_PORT" "$PANEL_FORWARD_PORT"; do
  [[ "$port" =~ ^[0-9]+$ ]] && ((port >= 1024 && port <= 65535)) \
    || die "invalid unprivileged host port: $port"
done

printf '%s  %s\n' "$IMAGE_SHA256" "$WORK_DIR/base.img" > "$WORK_DIR/image.sha256"
curl -fL --retry 5 --retry-all-errors --connect-timeout 20 \
  "$IMAGE_URL" -o "$WORK_DIR/base.img"
(cd "$WORK_DIR" && sha256sum -c image.sha256)
cp --reflink=auto "$WORK_DIR/base.img" "$WORK_DIR/disk.qcow2"
qemu-img resize "$WORK_DIR/disk.qcow2" "$VM_DISK_SIZE"

ssh-keygen -q -t ed25519 -N '' -f "$WORK_DIR/id_ed25519"
SSH_PUBLIC_KEY="$(cat "$WORK_DIR/id_ed25519.pub")"
cat > "$WORK_DIR/meta-data" <<EOF
instance-id: hostpanel-qemu-${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-1}
local-hostname: hostpanel-qemu
EOF
cat > "$WORK_DIR/user-data" <<EOF
#cloud-config
users:
  - default
  - name: hostpanel
    gecos: HostPanel acceptance
    groups: [adm, sudo]
    shell: /bin/bash
    sudo: ALL=(ALL) NOPASSWD:ALL
    ssh_authorized_keys:
      - ${SSH_PUBLIC_KEY}
ssh_pwauth: false
disable_root: true
package_update: false
package_upgrade: false
growpart:
  mode: auto
  devices: ['/']
resize_rootfs: true
EOF
cloud-localds "$WORK_DIR/seed.img" "$WORK_DIR/user-data" "$WORK_DIR/meta-data"

accel_args=(-accel tcg,thread=multi -cpu max)
if [[ -e /dev/kvm ]]; then
  sudo chmod a+rw /dev/kvm 2>/dev/null || true
  if [[ -r /dev/kvm && -w /dev/kvm ]]; then
    accel_args=(-enable-kvm -cpu host)
  fi
fi
printf 'QEMU acceleration: %s\n' "${accel_args[*]}"
qemu-system-x86_64 \
  -name hostpanel-acceptance \
  "${accel_args[@]}" \
  -machine type=q35 \
  -smp "$VM_CPUS" \
  -m "$VM_MEMORY_MB" \
  -drive "file=$WORK_DIR/disk.qcow2,if=virtio,format=qcow2,cache=writeback,discard=unmap" \
  -drive "file=$WORK_DIR/seed.img,if=virtio,format=raw,readonly=on" \
  -device virtio-net-pci,netdev=net0 \
  -netdev "user,id=net0,hostname=hostpanel-qemu,hostfwd=tcp:127.0.0.1:${SSH_PORT}-:22,hostfwd=tcp:127.0.0.1:${PANEL_FORWARD_PORT}-:2222,hostfwd=tcp:127.0.0.1:30025-:25,hostfwd=tcp:127.0.0.1:30143-:143,hostfwd=tcp:127.0.0.1:30993-:993,hostfwd=tcp:127.0.0.1:30080-:80,hostfwd=tcp:127.0.0.1:30443-:443" \
  -display none \
  -serial "file:$WORK_DIR/console.log" \
  -monitor none \
  -daemonize \
  -pidfile "$WORK_DIR/qemu.pid"
QEMU_PID="$(cat "$WORK_DIR/qemu.pid")"
kill -0 "$QEMU_PID"

wait_for_ssh(){
  local phase="$1"
  for _ in {1..180}; do
    if ssh "${ssh_opts[@]}" hostpanel@127.0.0.1 true >/dev/null 2>&1; then
      printf 'SSH is available (%s).\n' "$phase"
      return 0
    fi
    kill -0 "$QEMU_PID" 2>/dev/null || die "QEMU exited while waiting for SSH ($phase)"
    sleep 5
  done
  die "timed out waiting for SSH ($phase)"
}
wait_for_ssh 'initial boot'
ssh "${ssh_opts[@]}" hostpanel@127.0.0.1 'sudo cloud-init status --wait'
ssh "${ssh_opts[@]}" hostpanel@127.0.0.1 \
  'test "$(cat /proc/1/comm)" = systemd; cat /etc/os-release; uname -a; df -hT; free -h' \
  | tee "$ARTIFACT_DIR/pre-install-inventory.txt"

scp "${scp_opts[@]}" \
  "$REPO_ROOT/bootstrap-install.sh" \
  "$REPO_ROOT/tools/validate-production-vm.sh" \
  hostpanel@127.0.0.1:/tmp/
cat > "$WORK_DIR/guest.env" <<EOF
HP_REVIEWED_COMMIT_SHA=${REVIEWED_COMMIT_SHA}
HP_EXPECTED_VERSION=${EXPECTED_VERSION}
HP_PANEL_HOST=${PANEL_HOST}
HP_EXPECTED_PUBLIC_IP=${GUEST_IP}
HP_PANEL_ADMIN_CIDR=${ADMIN_CIDR}
HP_MTA=${MTA}
EOF
cat > "$WORK_DIR/guest-install.sh" <<'GUEST'
#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
source /tmp/guest.env
EVIDENCE=/root/hostpanel-qemu-evidence
PRIVATE_LOG=/root/hostpanel-qemu-private-install.log
install -d -o root -g root -m 700 "$EVIDENCE"
: > "$PRIVATE_LOG"
chmod 600 "$PRIVATE_LOG"
trap 'status=$?; printf "Guest installation failed at line %s (private installer output was not exported).\n" "$LINENO" >&2; exit "$status"' ERR

test "$(cat /proc/1/comm)" = systemd
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq >> "$PRIVATE_LOG" 2>&1
apt-get install -y -qq ca-certificates curl git openssl python3 >> "$PRIVATE_LOG" 2>&1
install -o root -g root -m 700 /tmp/bootstrap-install.sh /root/bootstrap-install.sh
install -o root -g root -m 700 /tmp/validate-production-vm.sh /root/validate-production-vm.sh

common_env=(
  HP_REPO_REF="$HP_REVIEWED_COMMIT_SHA"
  HP_PANEL_HOST="$HP_PANEL_HOST"
  HP_PANEL_ADMIN_CIDR="$HP_PANEL_ADMIN_CIDR"
  HP_MULTI_PHP_REPO=off
  HP_RSPAMD_REPO=off
)
install_args=(--mta "$HP_MTA")
echo 'Running installer preflight; detailed output stays in a root-only guest log.'
env "${common_env[@]}" bash /root/bootstrap-install.sh --check "${install_args[@]}" >> "$PRIVATE_LOG" 2>&1
echo 'Running full installation; generated credentials stay in the root-only guest log.'
env "${common_env[@]}" bash /root/bootstrap-install.sh "${install_args[@]}" >> "$PRIVATE_LOG" 2>&1

test "$(tr -d '[:space:]' < /opt/hostpanel/VERSION)" = "$HP_EXPECTED_VERSION"
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
GUEST
chmod 700 "$WORK_DIR/guest-install.sh"
chmod 600 "$WORK_DIR/guest.env"
scp "${scp_opts[@]}" \
  "$WORK_DIR/guest.env" "$WORK_DIR/guest-install.sh" \
  hostpanel@127.0.0.1:/tmp/
ssh "${ssh_opts[@]}" hostpanel@127.0.0.1 \
  'sudo chown root:root /tmp/guest.env /tmp/guest-install.sh && sudo chmod 600 /tmp/guest.env && sudo chmod 700 /tmp/guest-install.sh && sudo /tmp/guest-install.sh'

PRE_REBOOT_BOOT_ID="$(ssh "${ssh_opts[@]}" hostpanel@127.0.0.1 'cat /proc/sys/kernel/random/boot_id')"
ssh "${ssh_opts[@]}" hostpanel@127.0.0.1 'sudo systemctl reboot' || true
ssh_went_down=false
for _ in {1..60}; do
  if ! ssh "${ssh_opts[@]}" hostpanel@127.0.0.1 true >/dev/null 2>&1; then
    ssh_went_down=true
    break
  fi
  sleep 2
done
[[ "$ssh_went_down" == true ]] || die 'SSH never went down during reboot'
wait_for_ssh 'post reboot'
POST_REBOOT_BOOT_ID="$(ssh "${ssh_opts[@]}" hostpanel@127.0.0.1 'cat /proc/sys/kernel/random/boot_id')"
[[ -n "$PRE_REBOOT_BOOT_ID" && "$PRE_REBOOT_BOOT_ID" != "$POST_REBOOT_BOOT_ID" ]] \
  || die 'guest boot ID did not change'

ssh "${ssh_opts[@]}" hostpanel@127.0.0.1 \
  "sudo env HP_EXPECTED_VERSION='$EXPECTED_VERSION' HP_PANEL_HOST='$PANEL_HOST' HP_EXPECTED_PUBLIC_IP='$GUEST_IP' bash /root/validate-production-vm.sh --post-reboot | sudo tee /root/hostpanel-qemu-evidence/post-reboot-validator.txt"
ssh "${ssh_opts[@]}" hostpanel@127.0.0.1 \
  'sudo /opt/hostpanel/venv/bin/python /opt/hostpanel/app/hostpanel-doctor --quiet | sudo tee /root/hostpanel-qemu-evidence/doctor.txt'

nc -z -w 10 127.0.0.1 "$PANEL_FORWARD_PORT"
if ! curl -kfsS --retry 6 --connect-timeout 5 \
  --resolve "${PANEL_HOST}:${PANEL_FORWARD_PORT}:127.0.0.1" \
  "https://${PANEL_HOST}:${PANEL_FORWARD_PORT}/" \
  -o /dev/null; then
  curl -fsS --retry 6 --connect-timeout 5 \
    --resolve "${PANEL_HOST}:${PANEL_FORWARD_PORT}:127.0.0.1" \
    "http://${PANEL_HOST}:${PANEL_FORWARD_PORT}/" \
    -o /dev/null
fi
nc -z -w 10 127.0.0.1 30025
printf 'QEMU VM acceptance passed for %s (%s).\n' "$EXPECTED_VERSION" "$REVIEWED_COMMIT_SHA"
