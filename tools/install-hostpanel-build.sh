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
  hostpanel_build_ssl.py
  hostpanel_build_extras.py
  hostpanel_build_extras_state.py
  hostpanel_build_entry.py
  hostpanel_build_powerdns_adapter.py
  hostpanel_build_mongodb_adapter.py
)
EXECUTABLES=(
  hostpanel_build_web.py
  patch_custombuild_runtime.py
  patch_powerdns_runtime.py
  patch_varnish_runtime.py
  patch_extras_doctor.py
)
TARGET=/opt/hostpanel/tools/hostpanel-build
COMMAND=/usr/local/sbin/hostpanel-build
CONFIG=/etc/hostpanel/build.conf
MODE_FILE=/etc/hostpanel/webserver-mode
DNS_MODE_FILE=/etc/hostpanel/dns-mode
VARNISH_MODE_FILE=/etc/hostpanel/varnish-mode
MONGODB_MODE_FILE=/etc/hostpanel/mongodb-mode
PHP_STATE=/etc/hostpanel/php-versions

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
install -d -o root -g root -m 0700 \
  /etc/hostpanel /etc/hostpanel/ssl /var/backups/hostpanel/custombuild

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
install -o root -g root -m 0755 \
  "$SOURCE_ROOT/tools/patch_powerdns_runtime.py" \
  /opt/hostpanel/tools/patch-powerdns-runtime
install -o root -g root -m 0755 \
  "$SOURCE_ROOT/tools/patch_varnish_runtime.py" \
  /opt/hostpanel/tools/patch-varnish-runtime
install -o root -g root -m 0755 \
  "$SOURCE_ROOT/tools/patch_extras_doctor.py" \
  /opt/hostpanel/tools/patch-extras-doctor

cat >"$COMMAND" <<'EOF_LAUNCHER'
#!/usr/bin/env bash
set -Eeuo pipefail
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH
unset PYTHONPATH PYTHONHOME BASH_ENV ENV LD_PRELOAD LD_LIBRARY_PATH
exec /opt/hostpanel/tools/hostpanel-build "$@"
EOF_LAUNCHER
chown root:root "$COMMAND"
chmod 0755 "$COMMAND"

PHP_VERSIONS="$(python3 - "$PHP_STATE" <<'PY'
import pathlib
import re
import sys

path = pathlib.Path(sys.argv[1])
default = ['8.5', '8.4', '8.3', '8.2']
if path.is_file() and not path.is_symlink():
    values = [line.strip() for line in path.read_text(encoding='ascii').splitlines() if line.strip()]
else:
    values = default
pattern = re.compile(r'^(?:7\.4|8\.[0-5])$')
if not values or len(values) > 8 or len(values) != len(set(values)):
    raise SystemExit('invalid HostPanel PHP version state')
if any(pattern.fullmatch(value) is None for value in values):
    raise SystemExit('unsupported HostPanel PHP version state')
print(','.join(values))
PY
)" || {
  printf '%s\n' 'Error: could not derive CustomBuild PHP versions.' >&2
  exit 1
}

if [[ ! -e "$CONFIG" && ! -L "$CONFIG" ]]; then
  cat >"$CONFIG" <<EOF_CONFIG
# HostPanel CustomBuild-style service options.
# Managed with: hostpanel-build set KEY VALUE
webserver=nginx_apache
database=both
mta=postfix
dns=bind
mongodb=off
varnish=off
php_versions=$PHP_VERSIONS
EOF_CONFIG
  chown root:root "$CONFIG"
  chmod 0600 "$CONFIG"
else
  [[ -f "$CONFIG" && ! -L "$CONFIG" ]] || {
    printf 'Error: unsafe HostPanel build configuration: %s\n' "$CONFIG" >&2
    exit 1
  }
  [[ "$(stat -c %u:%g:%h -- "$CONFIG")" == 0:0:1 ]] || {
    printf 'Error: HostPanel build configuration has unsafe ownership or links: %s\n' "$CONFIG" >&2
    exit 1
  }
  chmod 0600 "$CONFIG"
fi

ensure_mode_file(){
  local path="$1" default="$2" pattern="$3" label="$4" value=""
  if [[ ! -e "$path" && ! -L "$path" ]]; then
    printf '%s\n' "$default" >"$path"
    chown root:root "$path"
    chmod 0644 "$path"
    return
  fi
  [[ -f "$path" && ! -L "$path" ]] || {
    printf 'Error: unsafe HostPanel %s mode file: %s\n' "$label" "$path" >&2
    exit 1
  }
  [[ "$(stat -c %u:%g:%h -- "$path")" == 0:0:1 ]] || {
    printf 'Error: HostPanel %s mode file has unsafe ownership or links: %s\n' "$label" "$path" >&2
    exit 1
  }
  value="$(tr -d '[:space:]' <"$path")"
  [[ "$value" =~ $pattern ]] || {
    printf 'Error: HostPanel %s mode file contains an invalid value: %s\n' "$label" "$path" >&2
    exit 1
  }
  chmod 0644 "$path"
}

WEB_MODE="$(awk -F= '$1=="webserver" {print $2; exit}' "$CONFIG")"
[[ "$WEB_MODE" =~ ^(nginx_apache|nginx|apache|openlitespeed)$ ]] || {
  printf '%s\n' 'Error: build.conf contains an invalid webserver option.' >&2
  exit 1
}
ensure_mode_file "$MODE_FILE" nginx_apache '^(nginx_apache|nginx|apache|openlitespeed)$' webserver

DNS_MODE="$(awk -F= '$1=="dns" {print $2; exit}' "$CONFIG")"
DNS_MODE="${DNS_MODE:-bind}"
[[ "$DNS_MODE" =~ ^(bind|powerdns)$ ]] || {
  printf '%s\n' 'Error: build.conf contains an invalid dns option.' >&2
  exit 1
}
ensure_mode_file "$DNS_MODE_FILE" bind '^(bind|powerdns)$' dns

MONGODB_MODE="$(awk -F= '$1=="mongodb" {print $2; exit}' "$CONFIG")"
MONGODB_MODE="${MONGODB_MODE:-off}"
[[ "$MONGODB_MODE" =~ ^(off|8\.0)$ ]] || {
  printf '%s\n' 'Error: build.conf contains an invalid mongodb option.' >&2
  exit 1
}
ensure_mode_file "$MONGODB_MODE_FILE" off '^(off|8\.0)$' mongodb

VARNISH_MODE="$(awk -F= '$1=="varnish" {print $2; exit}' "$CONFIG")"
VARNISH_MODE="${VARNISH_MODE:-off}"
[[ "$VARNISH_MODE" =~ ^(off|on)$ ]] || {
  printf '%s\n' 'Error: build.conf contains an invalid varnish option.' >&2
  exit 1
}
if [[ "$VARNISH_MODE" == on && "$WEB_MODE" == nginx ]]; then
  printf '%s\n' 'Error: varnish=on is incompatible with webserver=nginx.' >&2
  exit 1
fi
ensure_mode_file "$VARNISH_MODE_FILE" off '^(off|on)$' varnish

if [[ -d /opt/hostpanel/app ]]; then
  /opt/hostpanel/tools/patch-custombuild-runtime
  /opt/hostpanel/tools/patch-powerdns-runtime
  /opt/hostpanel/tools/patch-varnish-runtime
  /opt/hostpanel/tools/patch-extras-doctor
fi

APPLIED_WEB_MODE="$(tr -d '[:space:]' <"$MODE_FILE")"
APPLIED_DNS_MODE="$(tr -d '[:space:]' <"$DNS_MODE_FILE")"
APPLIED_MONGODB_MODE="$(tr -d '[:space:]' <"$MONGODB_MODE_FILE")"
APPLIED_VARNISH_MODE="$(tr -d '[:space:]' <"$VARNISH_MODE_FILE")"
printf '%s\n' 'HostPanel build tool installed as /usr/local/sbin/hostpanel-build.'
printf '%s\n' 'Base mode: nginx_apache.'
printf 'Selected web/DNS/MongoDB/Varnish: %s/%s/%s/%s. Applied: %s/%s/%s/%s. PHP branches: %s.\n' \
  "$WEB_MODE" "$DNS_MODE" "$MONGODB_MODE" "$VARNISH_MODE" \
  "$APPLIED_WEB_MODE" "$APPLIED_DNS_MODE" "$APPLIED_MONGODB_MODE" \
  "$APPLIED_VARNISH_MODE" "$PHP_VERSIONS"
printf '%s\n' 'Start with: sudo hostpanel-build versions'
