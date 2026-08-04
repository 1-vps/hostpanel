#!/usr/bin/env bash
# Install or refresh the signed GitHub release update agent.
set -euo pipefail

[[ ${EUID:-$(id -u)} -eq 0 ]] || {
  echo 'Error: install-update-agent.sh must run as root.' >&2
  exit 1
}

SOURCE_ROOT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
UPDATER_ENTRY="$SOURCE_ROOT/tools/hostpanel-update-entry.py"
UPDATER_IMPL="$SOURCE_ROOT/tools/hostpanel-update.py"
SERVICE="$SOURCE_ROOT/packaging/systemd/hostpanel-update.service"
TIMER="$SOURCE_ROOT/packaging/systemd/hostpanel-update.timer"
KEYRING="$SOURCE_ROOT/releases/update-keyring.json"

for path in "$UPDATER_ENTRY" "$UPDATER_IMPL" "$SERVICE" "$TIMER" "$KEYRING"; do
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

install -d -o root -g root -m 755 /opt/hostpanel/tools
install -d -o root -g root -m 700 /etc/hostpanel /var/lib/hostpanel
install -o root -g root -m 644 \
  "$UPDATER_IMPL" /opt/hostpanel/tools/hostpanel-update-impl.py
install -o root -g root -m 755 \
  "$UPDATER_ENTRY" /opt/hostpanel/tools/hostpanel-update
for name in "${KEY_FILES[@]}"; do
  install -o root -g root -m 644 \
    "$SOURCE_ROOT/releases/$name" "/etc/hostpanel/$name"
done
install -o root -g root -m 600 "$KEYRING" /etc/hostpanel/update-keyring.json
install -o root -g root -m 644 "$SERVICE" /etc/systemd/system/hostpanel-update.service
install -o root -g root -m 644 "$TIMER" /etc/systemd/system/hostpanel-update.timer

CONFIG=/etc/hostpanel/update-agent.conf
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
  [[ -f "$CONFIG" && ! -L "$CONFIG" ]] || {
    echo "Error: unsafe update-agent configuration: $CONFIG" >&2
    exit 1
  }
  chown root:root "$CONFIG"
  chmod 600 "$CONFIG"
  if ! grep -q '^HP_UPDATE_KEYRING=' "$CONFIG"; then
    printf '%s\n' 'HP_UPDATE_KEYRING=/etc/hostpanel/update-keyring.json' >>"$CONFIG"
  fi
fi

TOKEN_FILE=/etc/hostpanel/github-update.token
if [[ -e "$TOKEN_FILE" ]]; then
  [[ -f "$TOKEN_FILE" && ! -L "$TOKEN_FILE" ]] || {
    echo "Error: unsafe GitHub update token file: $TOKEN_FILE" >&2
    exit 1
  }
  chown root:root "$TOKEN_FILE"
  chmod 600 "$TOKEN_FILE"
fi

systemctl daemon-reload
systemctl enable --now hostpanel-update.timer
echo 'HostPanel signed update timer enabled.'
