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
MODULES=(
  hostpanel_build_config.py
  hostpanel_build_packages.py
  hostpanel_build_operations.py
  hostpanel_build_cli.py
)
EXECUTABLES=(
  hostpanel_build_web.py
  patch_custombuild_runtime.py
)
TARGET=/opt/hostpanel/tools/hostpanel-build
COMMAND=/usr/local/sbin/hostpanel-build
CONFIG=/etc/hostpanel/build.conf
MODE_FILE=/etc/hostpanel/webserver-mode

for path in "$SOURCE" "${MODULES[@]/#/$SOURCE_ROOT/tools/}" "${EXECUTABLES[@]/#/$SOURCE_ROOT/tools/}"; do
  [[ -f "$path" && ! -L "$path" ]] || {
    printf 'Error: unsafe or missing HostPanel build input: %s\n' "$path" >&2
    exit 1
  }
done
python3 -m py_compile \
  "$SOURCE" \
  "${MODULES[@]/#/$SOURCE_ROOT/tools/}" \
  "${EXECUTABLES[@]/#/$SOURCE_ROOT/tools/}"

install -d -o root -g root -m 0755 /opt/hostpanel/tools /usr/local/sbin
install -d -o root -g root -m 0700 /etc/hostpanel /var/backups/hostpanel/custombuild

if [[ "$(readlink -f -- "$SOURCE")" != "$TARGET" ]]; then
  install -o root -g root -m 0755 "$SOURCE" "$TARGET"
else
  chown root:root "$TARGET"
  chmod 0755 "$TARGET"
fi
for module in "${MODULES[@]}"; do
  install -o root -g root -m 0644 "$SOURCE_ROOT/tools/$module" "/opt/hostpanel/tools/$module"
done
install -o root -g root -m 0755 \
  "$SOURCE_ROOT/tools/hostpanel_build_web.py" \
  /opt/hostpanel/tools/hostpanel-build-web
install -o root -g root -m 0755 \
  "$SOURCE_ROOT/tools/patch_custombuild_runtime.py" \
  /opt/hostpanel/tools/patch-custombuild-runtime

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
webserver=nginx_apache
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

WEB_MODE="$(awk -F= '$1=="webserver" {print $2; exit}' "$CONFIG")"
[[ "$WEB_MODE" =~ ^(nginx_apache|nginx|apache|openlitespeed)$ ]] || {
  printf '%s\n' 'Error: build.conf contains an invalid webserver option.' >&2
  exit 1
}
printf '%s\n' "$WEB_MODE" >"$MODE_FILE"
chown root:root "$MODE_FILE"
chmod 0644 "$MODE_FILE"

if [[ -d /opt/hostpanel/app ]]; then
  /opt/hostpanel/tools/patch-custombuild-runtime
fi

printf '%s\n' 'HostPanel build tool installed as /usr/local/sbin/hostpanel-build.'
printf '%s\n' 'Base mode: nginx_apache. Start with: sudo hostpanel-build versions'
