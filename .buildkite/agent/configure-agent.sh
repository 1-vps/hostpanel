#!/usr/bin/env bash
set -euo pipefail

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
unset BASH_ENV ENV PYTHONPATH PYTHONHOME LD_PRELOAD LD_LIBRARY_PATH

fail() {
  printf 'HostPanel Buildkite agent configuration failed: %s\n' "$*" >&2
  exit 1
}

[[ "$(id -u)" == "0" ]] || fail "run as root"
[[ -r /etc/os-release ]] || fail "/etc/os-release is unavailable"
# shellcheck disable=SC1091
source /etc/os-release
[[ "${ID:-}" == "ubuntu" && "${VERSION_ID:-}" == "24.04" ]] || {
  fail "only Ubuntu 24.04 is supported by this provisioning script"
}

queue=""
pipeline_slug=""
token_file=""
verification_jwks=""
signing_jwks=""
signing_key_id=""
checkout_key=""

while (($#)); do
  case "$1" in
    --queue)
      (($# >= 2)) || fail "--queue requires a value"
      queue="$2"
      shift 2
      ;;
    --pipeline-slug)
      (($# >= 2)) || fail "--pipeline-slug requires a value"
      pipeline_slug="$2"
      shift 2
      ;;
    --token-file)
      (($# >= 2)) || fail "--token-file requires a value"
      token_file="$2"
      shift 2
      ;;
    --verification-jwks)
      (($# >= 2)) || fail "--verification-jwks requires a value"
      verification_jwks="$2"
      shift 2
      ;;
    --signing-jwks)
      (($# >= 2)) || fail "--signing-jwks requires a value"
      signing_jwks="$2"
      shift 2
      ;;
    --signing-key-id)
      (($# >= 2)) || fail "--signing-key-id requires a value"
      signing_key_id="$2"
      shift 2
      ;;
    --checkout-key)
      (($# >= 2)) || fail "--checkout-key requires a value"
      checkout_key="$2"
      shift 2
      ;;
    *) fail "unknown argument: $1" ;;
  esac
done

[[ "$queue" =~ ^hostpanel-(upload|ci|qemu)$ ]] || fail "invalid --queue"
[[ "$pipeline_slug" =~ ^[a-z0-9][a-z0-9-]{0,99}$ ]] || fail "invalid --pipeline-slug"
[[ -n "$token_file" && -f "$token_file" && ! -L "$token_file" ]] || fail "unsafe --token-file"
[[ -n "$verification_jwks" && -f "$verification_jwks" && ! -L "$verification_jwks" ]] || {
  fail "a regular --verification-jwks file is required"
}
if [[ "$queue" == "hostpanel-upload" ]]; then
  [[ -n "$signing_jwks" && -f "$signing_jwks" && ! -L "$signing_jwks" ]] || {
    fail "the upload queue requires --signing-jwks"
  }
  [[ "$signing_key_id" =~ ^[A-Za-z0-9._:-]{1,128}$ ]] || {
    fail "the upload queue requires a safe --signing-key-id"
  }
  [[ -z "$checkout_key" ]] || fail "the upload queue must not receive --checkout-key"
else
  [[ -z "$signing_jwks" && -z "$signing_key_id" ]] || {
    fail "runner queues must not receive signing key material"
  }
  [[ -n "$checkout_key" && -f "$checkout_key" && ! -L "$checkout_key" ]] || {
    fail "runner queues require a local --checkout-key"
  }
fi

command -v buildkite-agent >/dev/null 2>&1 || fail "install Buildkite agent before provisioning"
command -v python3 >/dev/null 2>&1 || fail "python3 is unavailable"
command -v mktemp >/dev/null 2>&1 || fail "mktemp is unavailable"
validated_input_dir="$(mktemp -d /run/hostpanel-buildkite-inputs.XXXXXX)"
chmod 0700 "$validated_input_dir"
cleanup_validated_inputs() {
  rm -rf -- "$validated_input_dir"
}
trap cleanup_validated_inputs EXIT HUP INT TERM

python3 - "$token_file" "$verification_jwks" "$signing_jwks" "$checkout_key" "$signing_key_id" "$queue" "$validated_input_dir" <<'PYFILES'
import json
import os
import pathlib
import stat
import sys

token_path, verification_path, signing_path, checkout_path, signing_key_id, queue, stage_dir = sys.argv[1:]


def fd_filesystem_type(fd: int) -> str:
    mnt_id = None
    with open(f"/proc/self/fdinfo/{fd}", "r", encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("mnt_id:"):
                mnt_id = line.split(":", 1)[1].strip()
                break
    if not mnt_id:
        raise SystemExit("cannot determine input file mount identity")

    with open("/proc/self/mountinfo", "r", encoding="utf-8") as handle:
        for line in handle:
            left, separator, right = line.rstrip("\n").partition(" - ")
            if not separator:
                continue
            fields = left.split()
            if fields and fields[0] == mnt_id:
                right_fields = right.split()
                if not right_fields:
                    break
                return right_fields[0]
    raise SystemExit("cannot resolve input file mount type")


stage = pathlib.Path(stage_dir)
stage_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
stage_flags |= getattr(os, "O_DIRECTORY", 0)
stage_fd = os.open(stage, stage_flags)
try:
    stage_info = os.fstat(stage_fd)
    if (
        not stat.S_ISDIR(stage_info.st_mode)
        or stage_info.st_uid != 0
        or stat.S_IMODE(stage_info.st_mode) != 0o700
    ):
        raise SystemExit("validated input staging directory is unsafe")
    if fd_filesystem_type(stage_fd) != "tmpfs":
        raise SystemExit("validated input staging directory must reside on tmpfs")
finally:
    os.close(stage_fd)


def read_regular(
    path_value: str,
    limit: int,
    *,
    private: bool = False,
    trusted: bool = False,
    require_tmpfs: bool = False,
) -> bytes:
    path = pathlib.Path(path_value)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        info = os.fstat(fd)
        mode = stat.S_IMODE(info.st_mode)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise SystemExit(f"unsafe input file: {path}")
        if trusted and (info.st_uid != 0 or mode & 0o022):
            raise SystemExit(f"trusted input must be root-owned and not group/world-writable: {path}")
        if private and (info.st_uid != 0 or mode != 0o600):
            raise SystemExit(f"private input must be root-owned mode 0600: {path}")
        if require_tmpfs and fd_filesystem_type(fd) != "tmpfs":
            raise SystemExit(f"--checkout-key must reside on tmpfs so it never reaches persistent worker storage: {path}")
        if info.st_size <= 0 or info.st_size > limit:
            raise SystemExit(f"unsafe input size: {path}")
        data = os.read(fd, limit + 1)
        after = os.fstat(fd)
        if (
            len(data) != info.st_size
            or len(data) > limit
            or after.st_dev != info.st_dev
            or after.st_ino != info.st_ino
            or after.st_size != info.st_size
            or after.st_mtime_ns != info.st_mtime_ns
            or after.st_ctime_ns != info.st_ctime_ns
        ):
            raise SystemExit(f"input changed while read: {path}")
        return data
    finally:
        os.close(fd)


def stage_bytes(name: str, data: bytes) -> None:
    destination = stage / name
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    fd = os.open(destination, flags, 0o600)
    try:
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise SystemExit(f"failed to stage validated input: {name}")
            view = view[written:]
        os.fsync(fd)
        info = os.fstat(fd)
        if info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1:
            raise SystemExit(f"unsafe staged input: {name}")
    finally:
        os.close(fd)


token_data = read_regular(token_path, 1024, private=True)
token = token_data.decode("utf-8", errors="strict").strip()
if not (20 <= len(token) <= 512) or not token.isascii() or any(ch.isspace() for ch in token):
    raise SystemExit("agent token has an unsafe format")
stage_bytes("agent-token", token_data)


def load_jwks(
    path_value: str,
    *,
    private: bool = False,
    trusted: bool = False,
) -> tuple[dict, bytes]:
    data = read_regular(path_value, 1024 * 1024, private=private, trusted=trusted)
    value = json.loads(data.decode("utf-8", errors="strict"))
    if not isinstance(value, dict) or not isinstance(value.get("keys"), list) or not value["keys"]:
        raise SystemExit(f"invalid JWKS: {path_value}")
    if not all(isinstance(item, dict) for item in value["keys"]):
        raise SystemExit(f"invalid JWKS key entry: {path_value}")
    return value, data


verification, verification_data = load_jwks(verification_path, trusted=True)
if any("d" in item for item in verification["keys"]):
    raise SystemExit("verification JWKS contains private key material")
stage_bytes("verification.jwks", verification_data)

if queue == "hostpanel-upload":
    signing, signing_data = load_jwks(signing_path, private=True)
    if not any(item.get("kid") == signing_key_id and "d" in item for item in signing["keys"]):
        raise SystemExit("signing JWKS lacks the requested private key")
    stage_bytes("signing.jwks", signing_data)
else:
    key = read_regular(checkout_path, 64 * 1024, private=True, require_tmpfs=True)
    if not key.startswith(b"-----BEGIN OPENSSH PRIVATE KEY-----"):
        raise SystemExit("checkout key is not an OpenSSH private key")
    stage_bytes("checkout-deploy-key", key)
PYFILES

staged_token_file="$validated_input_dir/agent-token"
staged_verification_jwks="$validated_input_dir/verification.jwks"
staged_signing_jwks="$validated_input_dir/signing.jwks"
staged_checkout_key="$validated_input_dir/checkout-deploy-key"

version_text="$(buildkite-agent --version 2>&1)"
version="$(printf '%s\n' "$version_text" | grep -Eo '[0-9]+\.[0-9]+\.[0-9]+' | head -n1)"
[[ -n "$version" ]] || fail "cannot parse Buildkite agent version"
python3 - "$version" <<'PYVER'
import sys
current = tuple(int(part) for part in sys.argv[1].split('.'))
if current < (3, 134, 0):
    raise SystemExit(f"Buildkite agent {sys.argv[1]} is older than 3.134.0")
PYVER

getent passwd buildkite-agent >/dev/null || fail "buildkite-agent user is missing"
install -d -o root -g buildkite-agent -m 0750 /etc/buildkite-agent
install -d -o root -g buildkite-agent -m 0750 /etc/buildkite-agent/hooks
install -d -o root -g buildkite-agent -m 0750 /etc/buildkite-agent/hostpanel-policy
install -d -o root -g buildkite-agent -m 0750 /etc/buildkite-agent/keys
install -d -o buildkite-agent -g buildkite-agent -m 0700 /var/lib/buildkite-agent/builds
install -d -o root -g buildkite-agent -m 0750 /var/lib/buildkite-agent/.ssh
install -d -o root -g buildkite-agent -m 0750 /run/hostpanel-buildkite
cat >/var/lib/buildkite-agent/.ssh/known_hosts <<'EOF_KNOWN_HOSTS'
github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl
github.com ecdsa-sha2-nistp256 AAAAE2VjZHNhLXNoYTItbmlzdHAyNTYAAAAIbmlzdHAyNTYAAABBBEmKSENjQEezOmxkZMy7opKgwFB9nkt5YRrYMjNuG5N87uRgg6CLrbo5wAdT/y6v0mKV0U2w0WZ2YB/++Tpockg=
github.com ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQCj7ndNxQowgcQnjshcLrqPEiiphnt+VTTvDP6mHBL9j1aNUkY4Ue1gvwnGLVlOhGeYrnZaMgRK6+PKCUXaDbC7qtbW8gIkhL7aGCsOr/C56SJMy/BCZfxd1nWzAOxSDPgVsmerOBYfNqltV9/hWCqBywINIR+5dIg6JTJ72pcEpEjcYgXkE2YEFXV1JHnsKgbLWNlhScqb2UmyRkQyytRLtL+38TGxkxCflmO+5Z8CSSNY7GidjMIZ7Q4zMjA2n1nGrlTDkzwDCsw+wqFPGQA179cnfGWOWRVruj16z6XyvxvjJwbz0wQZ75XK5tKSb7FNyeIEs4TT4jk+S4dhPeAUC5y+bDYirYgM4GC7uEnztnZyaVWQ7B381AK4Qdrwt51ZqExKbQpTUNn+EjqoTwvqNj4kqx5QUCI0ThS/YkOxJCXmPUWZbhjpCg56i+2aB6CmK2JGhn57K5mj0MNdBXA4/WnwH6XoPWJzK5Nyu2zB3nAZp+S5hpQs+p1vN1/wsjk=
EOF_KNOWN_HOSTS
chown root:buildkite-agent /var/lib/buildkite-agent/.ssh/known_hosts
chmod 0440 /var/lib/buildkite-agent/.ssh/known_hosts

token_destination=/etc/buildkite-agent/agent-token
[[ "$(readlink -f -- "$token_file")" != "$token_destination" ]] || {
  fail "--token-file must be a separate consumable input file"
}
install -o root -g root -m 0600 "$staged_token_file" "$token_destination"
rm -f -- "$token_file"
[[ ! -e "$token_file" ]] || fail "consumed --token-file still exists"

install -o root -g buildkite-agent -m 0640 "$staged_verification_jwks" /etc/buildkite-agent/keys/verification.jwks
if [[ "$queue" == "hostpanel-upload" ]]; then
  install -o root -g buildkite-agent -m 0640 "$staged_signing_jwks" /etc/buildkite-agent/keys/signing.jwks
  rm -f -- "$signing_jwks"
  [[ ! -e "$signing_jwks" ]] || fail "consumed --signing-jwks still exists"
  rm -f /run/hostpanel-buildkite/checkout-deploy-key
else
  rm -f /etc/buildkite-agent/keys/signing.jwks
  checkout_destination=/run/hostpanel-buildkite/checkout-deploy-key
  [[ "$(readlink -f -- "$checkout_key")" != "$checkout_destination" ]] || {
    fail "--checkout-key must be a separate consumable input file"
  }
  command -v findmnt >/dev/null 2>&1 || fail "findmnt is unavailable"
  command -v swapon >/dev/null 2>&1 || fail "swapon is unavailable"
  [[ "$(findmnt -n -o FSTYPE --target /run)" == "tmpfs" ]] || fail "/run must be tmpfs on runner workers"
  [[ -z "$(swapon --show=NAME --noheadings)" ]] || fail "runner workers must have swap disabled before receiving --checkout-key"
  install -o buildkite-agent -g buildkite-agent -m 0400 "$staged_checkout_key" "$checkout_destination"
  rm -f -- "$checkout_key"
  [[ ! -e "$checkout_key" ]] || fail "consumed --checkout-key still exists"
fi

printf '%s\n' "$queue" >/etc/buildkite-agent/hostpanel-policy/queue
printf '%s\n' "$pipeline_slug" >/etc/buildkite-agent/hostpanel-policy/pipeline-slug
chown root:buildkite-agent /etc/buildkite-agent/hostpanel-policy/queue \
  /etc/buildkite-agent/hostpanel-policy/pipeline-slug
chmod 0440 /etc/buildkite-agent/hostpanel-policy/queue \
  /etc/buildkite-agent/hostpanel-policy/pipeline-slug

script_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
for source_path in \
  "$script_root/.buildkite/agent/pre-bootstrap" \
  "$script_root/.buildkite/agent/environment-checkout-policy" \
  "$script_root/.buildkite/agent/checkout-private-repo" \
  "$script_root/.buildkite/agent/upload-trusted-pipeline.sh" \
  "$script_root/.buildkite/pipeline.yml"
do
  [[ -f "$source_path" && ! -L "$source_path" ]] || fail "unsafe provisioning source: $source_path"
done

install -o root -g buildkite-agent -m 0550 \
  "$script_root/.buildkite/agent/pre-bootstrap" \
  /etc/buildkite-agent/hooks/pre-bootstrap
install -o root -g buildkite-agent -m 0550 \
  "$script_root/.buildkite/agent/environment-checkout-policy" \
  /etc/buildkite-agent/hooks/environment

install -d -o root -g root -m 0755 /usr/local/libexec
if [[ "$queue" == "hostpanel-upload" ]]; then
  rm -f /etc/buildkite-agent/hooks/checkout /run/hostpanel-buildkite/checkout-deploy-key
  install -o root -g buildkite-agent -m 0550 \
    "$script_root/.buildkite/agent/upload-trusted-pipeline.sh" \
    /usr/local/libexec/hostpanel-upload-pipeline
  install -o root -g buildkite-agent -m 0440 \
    "$script_root/.buildkite/pipeline.yml" \
    /etc/buildkite-agent/hostpanel-pipeline.yml
  sha256sum /etc/buildkite-agent/hostpanel-pipeline.yml \
    | awk '{print $1}' \
    >/etc/buildkite-agent/hostpanel-policy/pipeline-sha256
  chown root:buildkite-agent /etc/buildkite-agent/hostpanel-policy/pipeline-sha256
  chmod 0440 /etc/buildkite-agent/hostpanel-policy/pipeline-sha256
else
  install -o root -g buildkite-agent -m 0550 \
    "$script_root/.buildkite/agent/checkout-private-repo" \
    /etc/buildkite-agent/hooks/checkout
  rm -f \
    /usr/local/libexec/hostpanel-upload-pipeline \
    /etc/buildkite-agent/hostpanel-pipeline.yml \
    /etc/buildkite-agent/hostpanel-policy/pipeline-sha256
fi

packages=(ca-certificates git python3 bash openssl curl openssh-client util-linux)
case "$queue" in
  hostpanel-upload) ;;
  hostpanel-ci) packages+=(docker.io shellcheck) ;;
  hostpanel-qemu) packages+=(qemu-system-x86 qemu-utils cloud-image-utils acl netcat-openbsd) ;;
esac
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends "${packages[@]}"

if [[ "$queue" == "hostpanel-ci" ]]; then
  usermod -aG docker buildkite-agent
  systemctl enable --now docker.service
fi
if [[ "$queue" == "hostpanel-qemu" ]]; then
  getent group kvm >/dev/null || fail "kvm group is unavailable"
  usermod -aG kvm buildkite-agent
fi

skip_checkout_setting=false
if [[ "$queue" == "hostpanel-upload" ]]; then
  skip_checkout_setting=true
fi
cat >/etc/buildkite-agent/buildkite-agent.cfg <<EOF_AGENT
token="fd://3"
name="${queue}-%hostname-%random"
queue="${queue}"
build-path="/var/lib/buildkite-agent/builds"
hooks-path="/etc/buildkite-agent/hooks"
no-command-eval=true
checkout-override-mode="strict"
no-plugins=true
no-local-hooks=true
no-git-submodules=true
no-ssh-keyscan=true
skip-checkout=${skip_checkout_setting}
allowed-repositories="^git@github\\.com:1-vps/hostpanel\\.git$"
strict-single-hooks=true
no-feature-reporting=true
disconnect-after-job=true
disconnect-after-idle-timeout=900
verification-jwks-file="/etc/buildkite-agent/keys/verification.jwks"
verification-failure-behavior="block"
EOF_AGENT
if [[ "$queue" == "hostpanel-upload" ]]; then
  cat >>/etc/buildkite-agent/buildkite-agent.cfg <<EOF_SIGN
signing-jwks-file="/etc/buildkite-agent/keys/signing.jwks"
signing-jwks-key-id="${signing_key_id}"
EOF_SIGN
fi
chown root:buildkite-agent /etc/buildkite-agent/buildkite-agent.cfg
chmod 0640 /etc/buildkite-agent/buildkite-agent.cfg

install -d -o root -g root -m 0755 /etc/systemd/system/buildkite-agent.service.d
cat >/etc/systemd/system/buildkite-agent.service.d/20-hostpanel-hardening.conf <<'EOF_SYSTEMD'
[Service]
Environment=HOME=/var/lib/buildkite-agent
UMask=0077
OpenFile=
OpenFile=/etc/buildkite-agent/agent-token:buildkite-agent-token:read-only
ExecStartPost=+/usr/bin/rm -f /etc/buildkite-agent/agent-token
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=full
ReadWritePaths=/var/lib/buildkite-agent
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectKernelLogs=true
ProtectControlGroups=true
ProtectClock=true
RestrictSUIDSGID=true
LockPersonality=true
RestrictRealtime=true
RemoveIPC=true
CapabilityBoundingSet=
AmbientCapabilities=
SystemCallArchitectures=native
LimitCORE=0
EOF_SYSTEMD
chmod 0644 /etc/systemd/system/buildkite-agent.service.d/20-hostpanel-hardening.conf

systemctl daemon-reload
systemd-analyze verify buildkite-agent.service >/dev/null
systemctl enable buildkite-agent.service
printf 'Configured %s for pipeline %s; bound wrapper must start the agent\n' "$queue" "$pipeline_slug"
