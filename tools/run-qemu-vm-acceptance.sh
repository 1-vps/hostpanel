#!/usr/bin/env bash
# Boot an ephemeral Ubuntu cloud image under QEMU, install HostPanel, reboot it,
# and run the production VM validator without external VPS credentials.
set -Eeuo pipefail

REPO_ROOT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
WORK_PARENT="${RUNNER_TEMP:-/tmp}"
WORK_DIR=""
ARTIFACT_ROOT="$REPO_ROOT/artifacts"
ARTIFACT_DIR="$ARTIFACT_ROOT/qemu-vm-acceptance"
IMAGE_URL="${HP_QEMU_IMAGE_URL:-https://cloud-images.ubuntu.com/releases/noble/release-20260725/ubuntu-24.04-server-cloudimg-amd64.img}"
IMAGE_SHA256="${HP_QEMU_IMAGE_SHA256:-d1940f7d69d343355e183dff1e08a59852d32e7309baa7a4bad8365b11b005ac}"
REVIEWED_COMMIT_SHA="${HP_QEMU_REVIEWED_COMMIT_SHA:-}"
EXPECTED_VERSION="${HP_QEMU_EXPECTED_VERSION:-3.4.0}"
MTA="${HP_QEMU_MTA:-postfix}"
REPO_TOKEN="${HP_QEMU_REPO_TOKEN:-}"
SSH_PORT="${HP_QEMU_SSH_PORT:-22022}"
PANEL_FORWARD_PORT="${HP_QEMU_PANEL_FORWARD_PORT:-32222}"
VM_MEMORY_MB="${HP_QEMU_MEMORY_MB:-8192}"
VM_CPUS="${HP_QEMU_CPUS:-4}"
VM_DISK_SIZE="${HP_QEMU_DISK_SIZE:-32G}"
PANEL_HOST="${HP_QEMU_PANEL_HOST:-panel.10.0.2.15.nip.io}"
GUEST_IP="${HP_QEMU_GUEST_IP:-10.0.2.15}"
ADMIN_CIDR="${HP_QEMU_ADMIN_CIDR:-10.0.2.2/32}"
QEMU_PID=""

die(){ printf 'Error: %s\n' "$*" >&2; exit 1; }
require(){ command -v "$1" >/dev/null 2>&1 || die "$1 is required"; }
[[ -d "$WORK_PARENT" && ! -L "$WORK_PARENT" ]] \
  || die "unsafe QEMU work parent: $WORK_PARENT"
WORK_DIR="$(mktemp -d "$WORK_PARENT/hostpanel-qemu-acceptance.XXXXXX")" \
  || die 'could not create a private QEMU work directory'
for artifact_path in "$ARTIFACT_ROOT" "$ARTIFACT_DIR"; do
  if [[ -e "$artifact_path" || -L "$artifact_path" ]]; then
    [[ -d "$artifact_path" && ! -L "$artifact_path" ]] \
      || die "unsafe artifact directory: $artifact_path"
  fi
done
mkdir -p "$ARTIFACT_DIR"
[[ -d "$ARTIFACT_ROOT" && ! -L "$ARTIFACT_ROOT" \
   && -d "$ARTIFACT_DIR" && ! -L "$ARTIFACT_DIR" ]] \
  || die 'artifact directories changed during setup'
chmod 700 "$WORK_DIR" "$ARTIFACT_DIR"
find "$ARTIFACT_DIR" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
# Keep the token as shell-only state before any long-lived child process starts.
# Process substitutions inherit only exported variables, so tee cannot retain it.
export -n HP_QEMU_REPO_TOKEN REPO_TOKEN 2>/dev/null || true
exec > >(tee "$ARTIFACT_DIR/runner.log") 2>&1

qemu_pid_is_ours(){
  local cmdline
  [[ "$QEMU_PID" =~ ^[0-9]+$ && -r "/proc/$QEMU_PID/cmdline" ]] || return 1
  cmdline="$(tr '\0' '\n' <"/proc/$QEMU_PID/cmdline" 2>/dev/null)" || return 1
  grep -Fq 'qemu-system-x86_64' <<<"$cmdline" \
    && grep -Fq "$WORK_DIR/disk.qcow2" <<<"$cmdline"
}
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

extract_guest_evidence(){
  python3 - "$WORK_DIR/guest-evidence.tgz" "$ARTIFACT_DIR" <<'PY'
import os
import pathlib
import shutil
import sys
import tarfile

archive_path = pathlib.Path(sys.argv[1])
destination = pathlib.Path(sys.argv[2])
max_members = 5000
max_total_size = 100 * 1024 * 1024

with tarfile.open(archive_path, "r:gz") as archive:
    members = archive.getmembers()
    if len(members) > max_members:
        raise SystemExit("guest evidence archive contains too many members")

    total_size = 0
    checked = []
    for member in members:
        path = pathlib.PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts:
            raise SystemExit(f"unsafe guest evidence path: {member.name}")
        if not path.parts or path.parts[0] != "hostpanel-qemu-evidence":
            raise SystemExit(f"unexpected guest evidence root: {member.name}")
        if member.issym() or member.islnk() or member.isdev() or member.isfifo():
            raise SystemExit(f"unsupported guest evidence member: {member.name}")
        if not (member.isdir() or member.isfile()):
            raise SystemExit(f"unsupported guest evidence type: {member.name}")
        total_size += max(member.size, 0)
        if total_size > max_total_size:
            raise SystemExit("guest evidence archive exceeds the size limit")
        checked.append((member, path))

    for member, path in checked:
        target = destination.joinpath(*path.parts)
        if member.isdir():
            target.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(target, 0o700)
            continue
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        source = archive.extractfile(member)
        if source is None:
            raise SystemExit(f"could not read guest evidence member: {member.name}")
        with source, target.open("xb") as output:
            shutil.copyfileobj(source, output)
        os.chmod(target, 0o600)
PY
}

collect_evidence(){
  set +e
  if [[ -n "$WORK_DIR" && -d "$WORK_DIR" && ! -L "$WORK_DIR" ]]; then
    rm -f -- "$WORK_DIR/guest.env"
  fi
  if qemu_pid_is_ours; then
    ssh "${ssh_opts[@]}" hostpanel@127.0.0.1 \
      'sudo tar -C /root -czf - hostpanel-qemu-evidence 2>/dev/null' \
      > "$WORK_DIR/guest-evidence.tgz" 2>/dev/null
    if [[ -s "$WORK_DIR/guest-evidence.tgz" ]]; then
      extract_guest_evidence || printf '%s\n' 'Guest evidence archive failed safety validation.' >&2
    fi
  fi
  [[ ! -f "$WORK_DIR/console.log" ]] || cp "$WORK_DIR/console.log" "$ARTIFACT_DIR/qemu-console.log"
  df -h > "$ARTIFACT_DIR/runner-disk.txt" 2>&1 || true
  if qemu_pid_is_ours; then
    kill "$QEMU_PID" 2>/dev/null || true
    for _ in {1..20}; do
      qemu_pid_is_ours || break
      sleep 1
    done
    qemu_pid_is_ours && kill -KILL "$QEMU_PID" 2>/dev/null || true
  fi
  if ! qemu_pid_is_ours && [[ -n "$WORK_DIR" && -d "$WORK_DIR" && ! -L "$WORK_DIR" ]]; then
    rm -rf -- "$WORK_DIR"
  fi
}
trap collect_evidence EXIT

for command in base64 curl qemu-img qemu-system-x86_64 cloud-localds ssh scp ssh-keygen nc python3 sha256sum tr; do
  require "$command"
done
[[ "$REVIEWED_COMMIT_SHA" =~ ^[0-9a-fA-F]{40}$ ]] \
  || die 'HP_QEMU_REVIEWED_COMMIT_SHA must be a reviewed full 40-character commit SHA'
[[ "$EXPECTED_VERSION" =~ ^[0-9]+(\.[0-9]+){2}([-+][0-9A-Za-z][0-9A-Za-z.-]*)?$ ]] \
  || die 'HP_QEMU_EXPECTED_VERSION must be a release version'
[[ -n "$REPO_TOKEN" ]] \
  || die 'HP_QEMU_REPO_TOKEN is required to fetch the reviewed private commit'
[[ "$REPO_TOKEN" != *$'\n'* && "$REPO_TOKEN" != *$'\r'* ]] \
  || die 'HP_QEMU_REPO_TOKEN contains an invalid line break'
unset HP_QEMU_REPO_TOKEN
export -n REPO_TOKEN 2>/dev/null || true
case "$MTA" in postfix|exim) ;; *) die "unsupported MTA: $MTA" ;; esac
HOST_FORWARD_PORTS=(
  "$SSH_PORT" "$PANEL_FORWARD_PORT" 30025 30143 30993 30080 30443
)
for port in "${HOST_FORWARD_PORTS[@]}"; do
  [[ "$port" =~ ^[0-9]+$ ]] && ((port >= 1024 && port <= 65535)) \
    || die "invalid unprivileged host port: $port"
done
python3 - "${HOST_FORWARD_PORTS[@]}" <<'PYPORTS' \
  || die 'QEMU host-forward ports must be unique and free on 127.0.0.1'
import socket
import sys

seen = set()
sockets = []
for raw in sys.argv[1:]:
    port = int(raw)
    if port in seen:
        raise SystemExit(f"duplicate QEMU host-forward port: {port}")
    seen.add(port)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port))
    except OSError as exc:
        raise SystemExit(f"QEMU host-forward port {port} is unavailable: {exc}") from exc
    sockets.append(sock)
PYPORTS
for source_file in \
  "$REPO_ROOT/bootstrap-install.sh" \
  "$REPO_ROOT/tools/validate-production-vm.sh" \
  "$REPO_ROOT/tools/secure-validation-io.py" \
  "$REPO_ROOT/tools/qemu-guest-install.sh"; do
  [[ -f "$source_file" && ! -L "$source_file" ]] || die "unsafe or missing source file: $source_file"
done

printf '%s  %s\n' "$IMAGE_SHA256" "$WORK_DIR/base.img" > "$WORK_DIR/image.sha256"
curl -fL --retry 5 --retry-all-errors --connect-timeout 20 \
  "$IMAGE_URL" -o "$WORK_DIR/base.img"
(cd "$WORK_DIR" && sha256sum -c image.sha256)
cp --reflink=auto "$WORK_DIR/base.img" "$WORK_DIR/disk.qcow2"
qemu-img resize "$WORK_DIR/disk.qcow2" "$VM_DISK_SIZE"

ssh-keygen -q -t ed25519 -N '' -f "$WORK_DIR/id_ed25519"
SSH_PUBLIC_KEY="$(cat "$WORK_DIR/id_ed25519.pub")"
cat > "$WORK_DIR/meta-data" <<EOF_META
instance-id: hostpanel-qemu-${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-1}
local-hostname: hostpanel-qemu
EOF_META
cat > "$WORK_DIR/user-data" <<EOF_USER
#cloud-config
manage_etc_hosts: true
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
EOF_USER
cloud-localds "$WORK_DIR/seed.img" "$WORK_DIR/user-data" "$WORK_DIR/meta-data"

accel_args=(-accel tcg,thread=multi -cpu max)
if [[ -c /dev/kvm && -r /dev/kvm && -w /dev/kvm ]]; then
  accel_args=(-enable-kvm -cpu host)
fi
printf 'QEMU acceleration: %s\n' "${accel_args[*]}"
env -u HP_QEMU_REPO_TOKEN -u REPO_TOKEN qemu-system-x86_64 \
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
qemu_pid_is_ours || die 'QEMU pidfile does not identify the expected VM process'

wait_for_ssh(){
  local phase="$1"
  for _ in {1..180}; do
    if ssh "${ssh_opts[@]}" hostpanel@127.0.0.1 true >/dev/null 2>&1; then
      printf 'SSH is available (%s).\n' "$phase"
      return 0
    fi
    qemu_pid_is_ours || die "QEMU exited while waiting for SSH ($phase)"
    sleep 5
  done
  die "timed out waiting for SSH ($phase)"
}
wait_for_ssh 'initial boot'
ssh "${ssh_opts[@]}" hostpanel@127.0.0.1 'sudo cloud-init status --wait'
ssh "${ssh_opts[@]}" hostpanel@127.0.0.1 \
  'test "$(cat /proc/1/comm)" = systemd; cat /etc/os-release; uname -a; df -hT; free -h' \
  | tee "$ARTIFACT_DIR/pre-install-inventory.txt"

REPO_AUTH_HEADER="$(printf 'x-access-token:%s' "$REPO_TOKEN" | base64 | tr -d '\r\n')"
{
  printf 'HP_REVIEWED_COMMIT_SHA=%q\n' "$REVIEWED_COMMIT_SHA"
  printf 'HP_EXPECTED_VERSION=%q\n' "$EXPECTED_VERSION"
  printf 'HP_PANEL_HOST=%q\n' "$PANEL_HOST"
  printf 'HP_EXPECTED_PUBLIC_IP=%q\n' "$GUEST_IP"
  printf 'HP_PANEL_ADMIN_CIDR=%q\n' "$ADMIN_CIDR"
  printf 'HP_MTA=%q\n' "$MTA"
  printf 'export GIT_CONFIG_COUNT=1\n'
  printf 'export GIT_CONFIG_KEY_0=%q\n' 'http.https://github.com/.extraheader'
  printf 'export GIT_CONFIG_VALUE_0=%q\n' "AUTHORIZATION: basic $REPO_AUTH_HEADER"
  printf 'export GIT_TERMINAL_PROMPT=0\n'
} > "$WORK_DIR/guest.env"
chmod 600 "$WORK_DIR/guest.env"
unset REPO_TOKEN REPO_AUTH_HEADER

scp "${scp_opts[@]}" \
  "$REPO_ROOT/bootstrap-install.sh" \
  "$REPO_ROOT/tools/validate-production-vm.sh" \
  "$REPO_ROOT/tools/secure-validation-io.py" \
  "$REPO_ROOT/tools/qemu-guest-install.sh" \
  "$WORK_DIR/guest.env" \
  hostpanel@127.0.0.1:/tmp/
rm -f -- "$WORK_DIR/guest.env"
ssh "${ssh_opts[@]}" hostpanel@127.0.0.1 '
set -eu
sudo python3 - "$(id -u)" <<PYROOT
import os
import pathlib
import stat
import sys
import tempfile

MAX_INPUT_BYTES = 16 * 1024 * 1024
expected_uid = int(sys.argv[1])
inputs = (
    (pathlib.Path("/tmp/bootstrap-install.sh"), 0o700),
    (pathlib.Path("/tmp/validate-production-vm.sh"), 0o700),
    (pathlib.Path("/tmp/secure-validation-io.py"), 0o700),
    (pathlib.Path("/tmp/qemu-guest-install.sh"), 0o700),
    (pathlib.Path("/tmp/guest.env"), 0o600),
)
for path, mode in inputs:
    source_fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    temp_name = None
    try:
        metadata = os.fstat(source_fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise SystemExit(f"QEMU guest input is not regular: {path}")
        if metadata.st_uid != expected_uid or metadata.st_nlink != 1:
            raise SystemExit(f"QEMU guest input ownership or link count is unsafe: {path}")
        if metadata.st_size > MAX_INPUT_BYTES:
            raise SystemExit(f"QEMU guest input is unexpectedly large: {path}")

        temp_fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        with os.fdopen(os.dup(source_fd), "rb") as source, os.fdopen(temp_fd, "wb") as target:
            remaining = metadata.st_size
            while remaining:
                chunk = source.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise SystemExit(f"QEMU guest input changed while copying: {path}")
                target.write(chunk)
                remaining -= len(chunk)
            if source.read(1):
                raise SystemExit(f"QEMU guest input grew while copying: {path}")
            target.flush()
            os.fsync(target.fileno())
        os.chmod(temp_name, mode)
        os.replace(temp_name, path)
        temp_name = None

        promoted = path.lstat()
        if (
            not stat.S_ISREG(promoted.st_mode)
            or promoted.st_uid != 0
            or promoted.st_gid != 0
            or promoted.st_nlink != 1
            or stat.S_IMODE(promoted.st_mode) != mode
        ):
            raise SystemExit(f"QEMU guest input promotion failed: {path}")
    finally:
        os.close(source_fd)
        if temp_name is not None:
            pathlib.Path(temp_name).unlink(missing_ok=True)
PYROOT
sudo /tmp/qemu-guest-install.sh
'

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

ssh "${ssh_opts[@]}" hostpanel@127.0.0.1 'sudo /root/hostpanel-qemu-post-reboot.sh'

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
