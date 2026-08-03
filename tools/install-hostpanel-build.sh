#!/usr/bin/env bash
# Install the HostPanel CustomBuild-style maintenance CLI.
set -Eeuo pipefail

PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH
umask 077
unset PYTHONPATH PYTHONHOME BASH_ENV ENV LD_PRELOAD LD_LIBRARY_PATH

[[ ${EUID:-$(id -u)} -eq 0 ]] || {
  printf '%s\n' 'Error: install-hostpanel-build.sh must run as root.' >&2
  exit 1
}

SOURCE_ROOT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
SOURCE="$SOURCE_ROOT/tools/hostpanel-build.py"
TARGET=/opt/hostpanel/tools/hostpanel-build
COMMAND=/usr/local/sbin/hostpanel-build
CONFIG=/etc/hostpanel/build.conf

[[ -f "$SOURCE" && ! -L "$SOURCE" ]] || {
  printf 'Error: unsafe or missing HostPanel build tool: %s\n' "$SOURCE" >&2
  exit 1
}
python3 -m py_compile "$SOURCE"

install -d -o root -g root -m 0755 /opt/hostpanel/tools /usr/local/sbin
install -d -o root -g root -m 0700 /etc/hostpanel /var/backups/hostpanel/custombuild

if [[ "$(readlink -f -- "$SOURCE")" != "$TARGET" ]]; then
  install -o root -g root -m 0755 "$SOURCE" "$TARGET"
else
  chown root:root "$TARGET"
  chmod 0755 "$TARGET"
fi

cat >"$COMMAND" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH
unset PYTHONPATH PYTHONHOME BASH_ENV ENV LD_PRELOAD LD_LIBRARY_PATH
exec /opt/hostpanel/tools/hostpanel-build "$@"
EOF
chown root:root "$COMMAND"
chmod 0755 "$COMMAND"

if [[ ! -e "$CONFIG" ]]; then
  cat >"$CONFIG" <<'EOF'
# HostPanel CustomBuild-style service options.
# Managed with: hostpanel-build set KEY VALUE
webservers=nginx,apache,openlitespeed
database=both
mta=postfix
php_versions=8.5,8.4,8.3,8.2
EOF
  chown root:root "$CONFIG"
  chmod 0600 "$CONFIG"
else
  [[ -f "$CONFIG" && ! -L "$CONFIG" ]] || {
    printf 'Error: unsafe HostPanel build configuration: %s\n' "$CONFIG" >&2
    exit 1
  }
  [[ "$(stat -c %u:%g -- "$CONFIG")" == 0:0 ]] || {
    printf 'Error: HostPanel build configuration is not root-owned: %s\n' "$CONFIG" >&2
    exit 1
  }
  chmod 0600 "$CONFIG"
fi

printf '%s\n' 'HostPanel build tool installed as /usr/local/sbin/hostpanel-build.'
printf '%s\n' 'Start with: sudo hostpanel-build versions'
