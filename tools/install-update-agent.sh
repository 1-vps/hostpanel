#!/usr/bin/env bash
# Install or refresh the signed GitHub release update agent.
set -euo pipefail

[[ ${EUID:-$(id -u)} -eq 0 ]] || {
  echo 'Error: install-update-agent.sh must run as root.' >&2
  exit 1
}

SOURCE_ROOT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
UPDATER="$SOURCE_ROOT/tools/hostpanel-update.py"
SERVICE="$SOURCE_ROOT/packaging/systemd/hostpanel-update.service"
TIMER="$SOURCE_ROOT/packaging/systemd/hostpanel-update.timer"
PUBLIC_KEY="$SOURCE_ROOT/releases/update.pub"

for path in "$UPDATER" "$SERVICE" "$TIMER" "$PUBLIC_KEY"; do
  [[ -f "$path" && ! -L "$path" ]] || {
    echo "Error: unsafe or missing update-agent input: $path" >&2
    exit 1
  }
done

install -d -o root -g root -m 755 /opt/hostpanel/tools
install -d -o root -g root -m 700 /etc/hostpanel /var/lib/hostpanel
install -o root -g root -m 755 "$UPDATER" /opt/hostpanel/tools/hostpanel-update
install -o root -g root -m 644 "$PUBLIC_KEY" /etc/hostpanel/update.pub
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
