#!/usr/bin/env bash
# Install or refresh the signed GitHub release update agent.
set -Eeuo pipefail

PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH
umask 077
unset PYTHONPATH PYTHONHOME BASH_ENV ENV LD_PRELOAD LD_LIBRARY_PATH

[[ ${EUID:-$(id -u)} -eq 0 ]] || {
  echo 'Error: install-update-agent.sh must run as root.' >&2
  exit 1
}

SOURCE_ROOT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
UPDATER_IMPL="$SOURCE_ROOT/tools/hostpanel-update.py"
UPDATER_ENTRY="$SOURCE_ROOT/tools/hostpanel-update-entry.py"
SERVICE="$SOURCE_ROOT/packaging/systemd/hostpanel-update.service"
TIMER="$SOURCE_ROOT/packaging/systemd/hostpanel-update.timer"
KEYRING="$SOURCE_ROOT/releases/update-keyring.json"
CONFIG=/etc/hostpanel/update-agent.conf
TOKEN_FILE=/etc/hostpanel/github-update.token

for path in "$UPDATER_IMPL" "$UPDATER_ENTRY" "$SERVICE" "$TIMER" "$KEYRING"; do
  [[ -f "$path" && ! -L "$path" ]] || {
    echo "Error: unsafe or missing update-agent input: $path" >&2
    exit 1
  }
done

mapfile -t KEY_FILES < <(
  python3 - "$KEYRING" <<'PY'
import json
import pathlib
import re
import sys

path = pathlib.Path(sys.argv[1])
data = json.loads(path.read_text(encoding="utf-8"))
if not isinstance(data, dict) or set(data) != {"schema", "keys"} or data["schema"] != 1:
    raise SystemExit("unsafe update keyring shape")
entries = data["keys"]
if not isinstance(entries, list) or not entries or len(entries) > 8:
    raise SystemExit("unsafe update key count")
seen = set()
for entry in entries:
    if not isinstance(entry, dict) or set(entry) != {
        "id", "file", "activate_from", "retire_after"
    }:
        raise SystemExit("unsafe update keyring entry")
    name = entry["file"]
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", name):
        raise SystemExit("unsafe update public-key filename")
    if name in seen:
        raise SystemExit("duplicate update public-key filename")
    seen.add(name)
    print(name)
PY
)

for name in "${KEY_FILES[@]}"; do
  path="$SOURCE_ROOT/releases/$name"
  [[ -f "$path" && ! -L "$path" ]] || {
    echo "Error: unsafe or missing update public key: $path" >&2
    exit 1
  }
done

if [[ -L "$CONFIG" ]]; then
  echo "Error: unsafe update-agent configuration symlink: $CONFIG" >&2
  exit 1
fi
if [[ -e "$CONFIG" ]]; then
  [[ -f "$CONFIG" && ! -L "$CONFIG" ]] || {
    echo "Error: unsafe update-agent configuration: $CONFIG" >&2
    exit 1
  }
  [[ "$(stat -c %u:%g:%h -- "$CONFIG")" == 0:0:1 ]] || {
    echo "Error: update-agent configuration has unsafe ownership or links: $CONFIG" >&2
    exit 1
  }
fi

if [[ -L "$TOKEN_FILE" ]]; then
  echo "Error: unsafe GitHub update token symlink: $TOKEN_FILE" >&2
  exit 1
fi
if [[ -e "$TOKEN_FILE" ]]; then
  [[ -f "$TOKEN_FILE" && ! -L "$TOKEN_FILE" ]] || {
    echo "Error: unsafe GitHub update token file: $TOKEN_FILE" >&2
    exit 1
  }
  [[ "$(stat -c %u:%g:%h -- "$TOKEN_FILE")" == 0:0:1 ]] || {
    echo "Error: GitHub update token has unsafe ownership or links: $TOKEN_FILE" >&2
    exit 1
  }
fi

install -d -o root -g root -m 755 /opt/hostpanel/tools
install -d -o root -g root -m 700 /etc/hostpanel /var/lib/hostpanel
install -o root -g root -m 644 \
  "$UPDATER_IMPL" /opt/hostpanel/tools/hostpanel-update-impl.py

python3 - "$UPDATER_ENTRY" /opt/hostpanel/tools/hostpanel-update <<'PYENTRY'
import os
import pathlib
import secrets
import stat
import sys

source = pathlib.Path(sys.argv[1])
destination = pathlib.Path(sys.argv[2])
maximum = 2 * 1024 * 1024

before = source.lstat()
if (
    not stat.S_ISREG(before.st_mode)
    or stat.S_ISLNK(before.st_mode)
    or before.st_nlink != 1
    or stat.S_IMODE(before.st_mode) & 0o022
    or before.st_size <= 0
    or before.st_size > maximum
):
    raise SystemExit(f'unsafe updater entry source: {source}')

flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, 'O_NOFOLLOW', 0)
source_fd = os.open(source, flags)
try:
    opened = os.fstat(source_fd)
    stable = (
        'st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid',
        'st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns',
    )
    if any(getattr(before, field) != getattr(opened, field) for field in stable):
        raise SystemExit('updater entry source changed before read')
    payload = bytearray()
    while True:
        chunk = os.read(source_fd, min(64 * 1024, maximum + 1 - len(payload)))
        if not chunk:
            break
        payload.extend(chunk)
        if len(payload) > maximum:
            raise SystemExit('updater entry source exceeds size limit')
    after = os.fstat(source_fd)
    current = source.lstat()
    if (
        any(getattr(opened, field) != getattr(after, field) for field in stable)
        or any(getattr(after, field) != getattr(current, field) for field in stable)
    ):
        raise SystemExit('updater entry source changed during read')
finally:
    os.close(source_fd)

parent = destination.parent
metadata = parent.lstat()
if (
    not stat.S_ISDIR(metadata.st_mode)
    or stat.S_ISLNK(metadata.st_mode)
    or metadata.st_uid != 0
    or metadata.st_gid != 0
    or stat.S_IMODE(metadata.st_mode) & 0o022
):
    raise SystemExit(f'unsafe updater destination directory: {parent}')
if os.path.lexists(destination):
    existing = destination.lstat()
    if (
        not stat.S_ISREG(existing.st_mode)
        or stat.S_ISLNK(existing.st_mode)
        or existing.st_uid != 0
        or existing.st_gid != 0
        or existing.st_nlink != 1
        or stat.S_IMODE(existing.st_mode) & 0o022
    ):
        raise SystemExit(f'unsafe updater entry destination: {destination}')

write_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
write_flags |= getattr(os, 'O_NOFOLLOW', 0)
for _ in range(32):
    temporary = destination.with_name(
        f'.{destination.name}.install.{secrets.token_hex(12)}'
    )
    try:
        fd = os.open(temporary, write_flags, 0o755)
        break
    except FileExistsError:
        continue
else:
    raise SystemExit('could not allocate updater entry temporary')

active_error = None
cleanup_error = None
try:
    view = memoryview(payload)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise SystemExit('could not write updater entrypoint')
        view = view[written:]
    os.fsync(fd)
    os.fchown(fd, 0, 0)
    os.fchmod(fd, 0o755)
    os.fsync(fd)
    descriptor, fd = fd, -1
    os.close(descriptor)
    os.replace(temporary, destination)
    directory_fd = os.open(
        parent,
        os.O_RDONLY | os.O_CLOEXEC
        | getattr(os, 'O_DIRECTORY', 0)
        | getattr(os, 'O_NOFOLLOW', 0),
    )
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
except BaseException as exc:
    active_error = exc
    raise
finally:
    if fd >= 0:
        descriptor, fd = fd, -1
        try:
            os.close(descriptor)
        except BaseException as exc:
            cleanup_error = exc
    try:
        temporary.unlink(missing_ok=True)
    except BaseException as exc:
        if cleanup_error is None:
            cleanup_error = exc
    if active_error is None and cleanup_error is not None:
        raise cleanup_error
PYENTRY

for name in "${KEY_FILES[@]}"; do
  install -o root -g root -m 644 \
    "$SOURCE_ROOT/releases/$name" "/etc/hostpanel/$name"
done
install -o root -g root -m 600 "$KEYRING" /etc/hostpanel/update-keyring.json
install -o root -g root -m 644 "$SERVICE" /etc/systemd/system/hostpanel-update.service
install -o root -g root -m 644 "$TIMER" /etc/systemd/system/hostpanel-update.timer

if [[ ! -e "$CONFIG" ]]; then
  cat >"$CONFIG" <<'EOF'
# HostPanel signed GitHub release updates.
HP_UPDATE_REPOSITORY=1-vps/hostpanel
HP_UPDATE_CHANNEL=stable
HP_UPDATE_TOKEN_FILE=/etc/hostpanel/github-update.token
HP_UPDATE_REQUIRE_TOKEN=yes
HP_UPDATE_PUBLIC_KEY=/etc/hostpanel/update.pub
HP_UPDATE_KEYRING=/etc/hostpanel/update-keyring.json
HP_AUTO_UPDATE=yes
EOF
  chown root:root "$CONFIG"
  chmod 600 "$CONFIG"
else
  chown root:root "$CONFIG"
  chmod 600 "$CONFIG"
  if ! grep -q '^HP_UPDATE_KEYRING=' "$CONFIG"; then
    printf '%s\n' 'HP_UPDATE_KEYRING=/etc/hostpanel/update-keyring.json' >>"$CONFIG"
  fi
fi

if [[ -e "$TOKEN_FILE" ]]; then
  chown root:root "$TOKEN_FILE"
  chmod 600 "$TOKEN_FILE"
fi

systemctl daemon-reload
systemctl enable --now hostpanel-update.timer
echo 'HostPanel signed update timer enabled.'
