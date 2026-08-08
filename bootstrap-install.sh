#!/usr/bin/env bash
# Fetch a reviewed HostPanel commit, verify the signed source archive with the
# embedded release trust root, verify every installer and localization overlay
# against the full Git object ID, and run the hardened installer from the complete source tree.
set -euo pipefail

PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH
umask 077
unset PYTHONPATH PYTHONHOME BASH_ENV ENV LD_PRELOAD LD_LIBRARY_PATH

REPO="${HP_REPO:-https://github.com/1-vps/hostpanel.git}"
REF="${HP_REPO_REF:-}"
WORK_DIR=""
PG_URL_FILE=""
INSTALLER_PID=""

# Long-lived release verification key. The adjacent key file in a fetched
# commit is deliberately not trusted to verify the archive from that commit.
read -r -d '' TRUSTED_RELEASE_PUBLIC_KEY <<'EOF' || true
-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAJonL5vK2NRcFkXvKZUs64ISOs+FfhwL8gQVmFO4C0qk=
-----END PUBLIC KEY-----
EOF

say(){ printf '\n==> %s\n' "$*"; }
die(){ printf '\nError: %s\n' "$*" >&2; exit 1; }
clear_git_auth_environment(){
  local name
  while IFS= read -r name; do unset "$name"; done < <(
    compgen -A variable GIT_CONFIG_KEY_ || true
  )
  while IFS= read -r name; do unset "$name"; done < <(
    compgen -A variable GIT_CONFIG_VALUE_ || true
  )
  unset GIT_CONFIG_COUNT GIT_TERMINAL_PROMPT
  unset GH_READ_TOKEN GH_TOKEN GITHUB_TOKEN GIT_AUTH_HEADER
}
cleanup(){
  [[ -z "$PG_URL_FILE" ]] || rm -f -- "$PG_URL_FILE"
  [[ -z "$WORK_DIR" ]] || rm -rf -- "$WORK_DIR"
}
signal_exit(){
  local status="$1"
  trap - EXIT HUP INT TERM
  if [[ "$INSTALLER_PID" =~ ^[0-9]+$ ]]; then
    kill -TERM "$INSTALLER_PID" 2>/dev/null || true
    wait "$INSTALLER_PID" 2>/dev/null || true
    INSTALLER_PID=""
  fi
  cleanup
  exit "$status"
}
trap cleanup EXIT
trap 'signal_exit 129' HUP
trap 'signal_exit 130' INT
trap 'signal_exit 143' TERM

postgres_diagnostics(){
  printf '\n--- PostgreSQL diagnostics ---\n' >&2
  systemctl status postgresql --no-pager -l >&2 || true
  journalctl -u postgresql -n 80 --no-pager >&2 || true
  sudo -u postgres psql -Atqc 'SELECT version();' >&2 || true
  printf '%s\n' '--- end PostgreSQL diagnostics ---' >&2
}

repair_preserved_local_postgres(){
  local credential=/opt/hostpanel/credentials/database-url
  local runtime_python=/opt/hostpanel/venv/bin/python
  local result="" rc=0

  [[ -s "$credential" && -x "$runtime_python" ]] || return 0
  command -v sudo >/dev/null 2>&1 \
    || die "sudo is required to reconcile the preserved PostgreSQL control plane"
  id postgres >/dev/null 2>&1 \
    || die "The postgres service account is missing"

  systemctl start postgresql >/dev/null 2>&1 || true
  PG_URL_FILE="$(mktemp /run/hostpanel-control-url.XXXXXX)" \
    || die "Could not create a private PostgreSQL credential handoff"
  chmod 600 "$PG_URL_FILE"
  head -1 "$credential" >"$PG_URL_FILE"
  chown postgres:postgres "$PG_URL_FILE"

  if result="$(sudo -u postgres "$runtime_python" - "$PG_URL_FILE" <<'PYPGREPAIR'
import pathlib
import sys
from urllib.parse import unquote, urlsplit

import psycopg
from psycopg import sql

url = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8").strip()
try:
    parsed = urlsplit(url)
    port = parsed.port
except ValueError as exc:
    raise SystemExit(f"invalid preserved PostgreSQL URL: {exc}")

is_local_hostpanel = (
    parsed.scheme in {"postgres", "postgresql"}
    and parsed.username == "hostpanel_control"
    and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    and port in {None, 5432}
    and parsed.path == "/hostpanel_control"
    and not parsed.query
    and not parsed.fragment
)
if not is_local_hostpanel:
    print("skipped")
    raise SystemExit(0)

password = unquote(parsed.password or "")
if not password or any(ord(character) < 32 for character in password):
    raise SystemExit("the preserved local PostgreSQL password is empty or contains control characters")

role_name = "hostpanel_control"
database_name = "hostpanel_control"
with psycopg.connect("dbname=postgres", autocommit=True) as connection:
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role_name,))
        if cursor.fetchone() is None:
            cursor.execute(sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(role_name)))
        cursor.execute(
            sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(
                sql.Identifier(role_name), sql.Literal(password)
            )
        )
        cursor.execute("SELECT 1 FROM pg_database WHERE datname=%s", (database_name,))
        if cursor.fetchone() is None:
            cursor.execute(
                sql.SQL("CREATE DATABASE {} OWNER {}").format(
                    sql.Identifier(database_name), sql.Identifier(role_name)
                )
            )
        else:
            cursor.execute(
                sql.SQL("ALTER DATABASE {} OWNER TO {}").format(
                    sql.Identifier(database_name), sql.Identifier(role_name)
                )
            )

with psycopg.connect(url, connect_timeout=5) as connection:
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_user, current_database()")
        identity = cursor.fetchone()
if identity != (role_name, database_name):
    raise SystemExit("the repaired PostgreSQL credential resolved to an unexpected identity")
print("repaired")
PYPGREPAIR
  )"; then
    rc=0
  else
    rc=$?
  fi

  rm -f -- "$PG_URL_FILE"
  PG_URL_FILE=""
  if ((rc != 0)); then
    postgres_diagnostics
    die "Could not reconcile the preserved local PostgreSQL control plane"
  fi
  [[ "$result" != repaired ]] || printf '  ok preserved local PostgreSQL role, database and password reconciled\n'
}

[[ "$REPO" == "https://github.com/1-vps/hostpanel.git" ]] \
  || die "HP_REPO must be the canonical HostPanel repository"
[[ "$REF" =~ ^[0-9a-fA-F]{40}$ ]] \
  || die "Set HP_REPO_REF to the reviewed full 40-character Git commit SHA"

for command in git sha256sum openssl python3 mktemp tar; do
  command -v "$command" >/dev/null 2>&1 \
    || die "$command is required before running the bootstrap"
done

WORK_DIR="$(mktemp -d /tmp/hostpanel-bootstrap.XXXXXX)" \
  || die "Could not create a private bootstrap directory"
chmod 700 "$WORK_DIR"
GIT_HOME="$WORK_DIR/git-home"
mkdir -m 700 "$GIT_HOME"
reviewed_git(){
  env HOME="$GIT_HOME" GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null \
    git "$@"
}
CHECKOUT="$WORK_DIR/repository"
EXTRACT_ROOT="$WORK_DIR/release"
PUBLIC_KEY="$WORK_DIR/trusted-release.pub"
mkdir -p "$CHECKOUT" "$EXTRACT_ROOT"
printf '%s\n' "$TRUSTED_RELEASE_PUBLIC_KEY" >"$PUBLIC_KEY"
chmod 600 "$PUBLIC_KEY"

say "Fetching reviewed HostPanel commit $REF"
reviewed_git -C "$CHECKOUT" init -q \
  || die "Could not initialise the temporary Git checkout"
reviewed_git -C "$CHECKOUT" remote add origin "$REPO" \
  || die "Could not configure the reviewed Git repository"
reviewed_git -C "$CHECKOUT" fetch --depth 1 origin "$REF" \
  || die "Could not fetch the reviewed commit from the canonical repository"
FETCHED_COMMIT="$(reviewed_git -C "$CHECKOUT" rev-parse --verify 'FETCH_HEAD^{commit}')" \
  || die "Could not resolve the fetched Git commit"
[[ "${FETCHED_COMMIT,,}" == "${REF,,}" ]] \
  || die "Fetched Git commit does not match HP_REPO_REF"
reviewed_git -C "$CHECKOUT" checkout -q --detach "$FETCHED_COMMIT" \
  || die "Could not check out reviewed commit $REF"
clear_git_auth_environment

CHECKSUMS="$CHECKOUT/SHA256SUMS"
[[ -s "$CHECKSUMS" ]] || die "Reviewed commit is missing SHA256SUMS"
mapfile -t SOURCE_ARCHIVES < <(
  awk '$2 ~ /^hostpanel-v[0-9]+\.[0-9]+\.[0-9]+([-+][0-9A-Za-z][0-9A-Za-z.-]*)?-source\.tar\.gz$/ {print $2}' "$CHECKSUMS"
)
((${#SOURCE_ARCHIVES[@]} == 1)) \
  || die "SHA256SUMS must identify exactly one HostPanel source tarball"
ARCHIVE_NAME="${SOURCE_ARCHIVES[0]}"
ARCHIVE_RELEASE_ID="${ARCHIVE_NAME#hostpanel-v}"
ARCHIVE_RELEASE_ID="${ARCHIVE_RELEASE_ID%-source.tar.gz}"
[[ "$ARCHIVE_RELEASE_ID" =~ ^[0-9]+(\.[0-9]+){2}([-+][0-9A-Za-z][0-9A-Za-z.-]*)?$ ]] \
  || die "Source archive name contains an invalid release identifier"

ARCHIVE="$CHECKOUT/$ARCHIVE_NAME"
SIGNATURE="$ARCHIVE.sig"
[[ -s "$ARCHIVE" && -s "$SIGNATURE" ]] \
  || die "Reviewed commit lacks the signed source-release files"

say "Verifying source archive checksum and anchored signature"
CHECKSUM_LINE="$(awk -v name="$ARCHIVE_NAME" '$2 == name {line=$0; count++} END {if (count != 1) exit 1; print line}' "$CHECKSUMS")" \
  || die "SHA256SUMS must contain exactly one entry for $ARCHIVE_NAME"
printf '%s\n' "$CHECKSUM_LINE" | (cd "$CHECKOUT" && sha256sum -c -) \
  || die "Source archive checksum verification failed"
openssl pkeyutl -verify -pubin -inkey "$PUBLIC_KEY" -rawin \
  -in "$ARCHIVE" -sigfile "$SIGNATURE" >/dev/null \
  || die "Source archive signature verification failed"

say "Extracting verified source release"
python3 - "$ARCHIVE" "$EXTRACT_ROOT" <<'PYARCHIVE'
import inspect
import pathlib
import sys
import tarfile

archive = pathlib.Path(sys.argv[1])
destination = pathlib.Path(sys.argv[2])
max_members = 50000
max_total_size = 2 * 1024 * 1024 * 1024
with tarfile.open(archive, "r:gz") as handle:
    members = handle.getmembers()
    if len(members) > max_members:
        raise SystemExit("source archive contains too many members")
    total_size = 0
    for member in members:
        path = pathlib.PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts:
            raise SystemExit(f"unsafe archive path: {member.name}")
        if member.issym() or member.islnk() or member.isdev() or member.isfifo():
            raise SystemExit(f"unsupported archive member: {member.name}")
        total_size += max(member.size, 0)
        if total_size > max_total_size:
            raise SystemExit("source archive expands beyond the safety limit")
    options = {}
    if "filter" in inspect.signature(handle.extractall).parameters:
        options["filter"] = "fully_trusted"
    handle.extractall(destination, members=members, **options)
PYARCHIVE

mapfile -t RELEASE_ROOTS < <(find "$EXTRACT_ROOT" -mindepth 1 -maxdepth 1 -type d -print)
((${#RELEASE_ROOTS[@]} == 1)) \
  || die "Source archive must contain exactly one top-level directory"
SOURCE_ROOT="${RELEASE_ROOTS[0]}"
EXPECTED_ROOT_NAME="${ARCHIVE_NAME%-source.tar.gz}"
[[ "${SOURCE_ROOT##*/}" == "$EXPECTED_ROOT_NAME" ]] \
  || die "Source archive top-level directory does not match its signed filename"
[[ -d "$SOURCE_ROOT/app" && -s "$SOURCE_ROOT/VERSION" && -s "$SOURCE_ROOT/requirements.lock" ]] \
  || die "Verified source archive is incomplete"
SOURCE_VERSION="$(tr -d '[:space:]' <"$SOURCE_ROOT/VERSION")"
[[ "$SOURCE_VERSION" =~ ^[0-9]+(\.[0-9]+){2}([-+][0-9A-Za-z][0-9A-Za-z.-]*)?$ ]] \
  || die "Verified source archive contains an invalid VERSION"
[[ "${SOURCE_VERSION%%[-+]*}" == "${ARCHIVE_RELEASE_ID%%[-+]*}" ]] \
  || die "Verified source archive VERSION is incompatible with its signed filename"

RELEASE_VERSION_FILE="$CHECKOUT/RELEASE_VERSION"
[[ -f "$RELEASE_VERSION_FILE" && ! -L "$RELEASE_VERSION_FILE" ]] \
  || die "Reviewed commit is missing a safe RELEASE_VERSION"
RELEASE_VERSION_EXPECTED="$(reviewed_git -C "$CHECKOUT" rev-parse "$FETCHED_COMMIT:RELEASE_VERSION" 2>/dev/null)" \
  || die "Reviewed commit does not contain RELEASE_VERSION"
RELEASE_VERSION_ACTUAL="$(reviewed_git -C "$CHECKOUT" hash-object "$RELEASE_VERSION_FILE")" \
  || die "Could not hash reviewed RELEASE_VERSION"
[[ "$RELEASE_VERSION_ACTUAL" == "$RELEASE_VERSION_EXPECTED" ]] \
  || die "RELEASE_VERSION does not match its reviewed Git object"
DEPLOY_VERSION="$(tr -d '[:space:]' <"$RELEASE_VERSION_FILE")"
[[ "$DEPLOY_VERSION" =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]] \
  || die "RELEASE_VERSION must be a final strict semantic version"
python3 - "$SOURCE_VERSION" "$DEPLOY_VERSION" <<'PYVERSION'
import re
import sys

pattern = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
source_match = pattern.fullmatch(sys.argv[1])
deploy_match = pattern.fullmatch(sys.argv[2])
if source_match is None or deploy_match is None:
    raise SystemExit("base and deploy versions must be final strict semantic versions")
source = tuple(int(part) for part in source_match.groups())
deploy = tuple(int(part) for part in deploy_match.groups())
if not source < deploy:
    raise SystemExit("RELEASE_VERSION must be greater than the signed source VERSION")
PYVERSION
printf '%s\n' "$DEPLOY_VERSION" >"$SOURCE_ROOT/VERSION"
chmod 0644 "$SOURCE_ROOT/VERSION"

verify_commit_file(){
  local path="$1" expected actual
  [[ -f "$CHECKOUT/$path" && ! -L "$CHECKOUT/$path" ]] \
    || die "Reviewed commit contains an unsafe overlay path: $path"
  expected="$(reviewed_git -C "$CHECKOUT" rev-parse "$FETCHED_COMMIT:$path" 2>/dev/null)" \
    || die "Reviewed commit does not contain $path"
  actual="$(reviewed_git -C "$CHECKOUT" hash-object "$CHECKOUT/$path")" \
    || die "Could not hash reviewed overlay $path"
  [[ "$actual" == "$expected" ]] \
    || die "Reviewed overlay does not match its Git object: $path"
}

say "Verifying and applying commit-addressed installer overlays"
OVERLAY_FILES=(
  install.sh
  install.base.sh
  tools/harden_install.py
  tools/harden_install_impl.py
  tools/harden_install_runtime.py
  tools/patch_cli_runtime_env.py
  tools/patch_product_fixes.py
  tools/patch_panel_ui.py
  tools/hostpanel-update.py
  tools/install-update-agent.sh
  packaging/systemd/hostpanel-update.service
  packaging/systemd/hostpanel-update.timer
  releases/update.pub
  releases/update-keyring.json
  app/static/panel-redesign.css
  app/static/panel-redesign.js
  app/static/hostpanel-brand.css
  app/static/hostpanel-mark.svg
  app/static/hostpanel-logo.svg
)
for overlay in "${OVERLAY_FILES[@]}"; do verify_commit_file "$overlay"; done
install -m 0755 "$CHECKOUT/install.sh" "$SOURCE_ROOT/install.sh"
install -m 0755 "$CHECKOUT/install.base.sh" "$SOURCE_ROOT/install.base.sh"
install -d -m 0755 "$SOURCE_ROOT/tools" "$SOURCE_ROOT/app/static" \
  "$SOURCE_ROOT/packaging/systemd" "$SOURCE_ROOT/releases"
install -m 0755 "$CHECKOUT/tools/harden_install.py" "$SOURCE_ROOT/tools/harden_install.py"
install -m 0644 "$CHECKOUT/tools/harden_install_impl.py" "$SOURCE_ROOT/tools/harden_install_impl.py"
install -m 0755 "$CHECKOUT/tools/harden_install_runtime.py" "$SOURCE_ROOT/tools/harden_install_runtime.py"
install -m 0755 "$CHECKOUT/tools/patch_cli_runtime_env.py" "$SOURCE_ROOT/tools/patch_cli_runtime_env.py"
install -m 0755 "$CHECKOUT/tools/patch_product_fixes.py" "$SOURCE_ROOT/tools/patch_product_fixes.py"
install -m 0755 "$CHECKOUT/tools/patch_panel_ui.py" "$SOURCE_ROOT/tools/patch_panel_ui.py"
install -m 0755 "$CHECKOUT/tools/hostpanel-update.py" "$SOURCE_ROOT/tools/hostpanel-update.py"
install -m 0755 "$CHECKOUT/tools/install-update-agent.sh" "$SOURCE_ROOT/tools/install-update-agent.sh"
install -m 0644 "$CHECKOUT/packaging/systemd/hostpanel-update.service" \
  "$SOURCE_ROOT/packaging/systemd/hostpanel-update.service"
install -m 0644 "$CHECKOUT/packaging/systemd/hostpanel-update.timer" \
  "$SOURCE_ROOT/packaging/systemd/hostpanel-update.timer"
install -m 0644 "$CHECKOUT/releases/update.pub" "$SOURCE_ROOT/releases/update.pub"
install -m 0644 "$CHECKOUT/releases/update-keyring.json" \
  "$SOURCE_ROOT/releases/update-keyring.json"
install -m 0644 "$CHECKOUT/app/static/panel-redesign.css" "$SOURCE_ROOT/app/static/panel-redesign.css"
install -m 0644 "$CHECKOUT/app/static/panel-redesign.js" "$SOURCE_ROOT/app/static/panel-redesign.js"
install -m 0644 "$CHECKOUT/app/static/hostpanel-brand.css" "$SOURCE_ROOT/app/static/hostpanel-brand.css"
install -m 0644 "$CHECKOUT/app/static/hostpanel-mark.svg" "$SOURCE_ROOT/app/static/hostpanel-mark.svg"
install -m 0644 "$CHECKOUT/app/static/hostpanel-logo.svg" "$SOURCE_ROOT/app/static/hostpanel-logo.svg"
python3 "$SOURCE_ROOT/tools/patch_product_fixes.py" "$SOURCE_ROOT" \
  || die "Could not apply the reviewed product security and performance fixes"
python3 "$SOURCE_ROOT/tools/patch_panel_ui.py" "$SOURCE_ROOT/app/templates/panel.html" \
  || die "Could not apply the reviewed control-panel UI overlay"

LOCALIZATION_ROOT="$CHECKOUT/localization-overlay"
LOCALIZATION_FILES=(
  localization-overlay/apply_localization_overlay.py
  localization-overlay/apply_localization_overlay_reviewed.py
  localization-overlay/review_locales.py
  localization-overlay/LOCALIZATION.md
  localization-overlay/LOCALIZATION-REVIEW-v3.4.0-overlay.md
  localization-overlay/login-messages.json
  localization-overlay/catalog-overrides.json
  localization-overlay/catalog-final-overrides.ja-01.json
  localization-overlay/catalog-final-overrides.ja-02.json
  localization-overlay/catalog-final-overrides.pt-01.json
  localization-overlay/catalog-final-overrides.zh-01.json
  localization-overlay/catalog-final-overrides.zh-02.json
  localization-overlay/catalog-overrides.json.gz.b64.chunk01
  localization-overlay/catalog-overrides.json.gz.b64.chunk02
  localization-overlay/catalog-overrides.json.gz.b64.chunk03
  localization-overlay/catalog-overrides.json.gz.b64.chunk04a
  localization-overlay/catalog-overrides.json.gz.b64.chunk04b
  localization-overlay/catalog-overrides.json.gz.b64.chunk04c
  localization-overlay/catalog-overrides.json.gz.b64.chunk04d
  localization-overlay/catalog-overrides.json.gz.b64.chunk05
  localization-overlay/catalog-overrides.json.gz.b64.chunk06
  localization-overlay/catalog-overrides.json.gz.b64.chunk07a
  localization-overlay/catalog-overrides.json.gz.b64.chunk07b
  localization-overlay/catalog-overrides.json.gz.b64.chunk07c
  localization-overlay/catalog-overrides.json.gz.b64.chunk07d
  localization-overlay/catalog-overrides.json.gz.b64.chunk08a
  localization-overlay/catalog-overrides.json.gz.b64.chunk08b
  localization-overlay/catalog-overrides.json.gz.b64.chunk08c
  localization-overlay/catalog-overrides.json.gz.b64.chunk08d
  localization-overlay/existing-catalog-corrections.json
  localization-overlay/existing-catalog-corrections.sv-ui-1.json
  localization-overlay/existing-catalog-corrections.sv-ui-2.json
  localization-overlay/catalogs/i18n.ja.json
  localization-overlay/catalogs/i18n.pt.json
  localization-overlay/catalogs/i18n.zh.json
)
for overlay in "${LOCALIZATION_FILES[@]}"; do verify_commit_file "$overlay"; done
python3 "$LOCALIZATION_ROOT/apply_localization_overlay_reviewed.py" \
  "$SOURCE_ROOT" "$LOCALIZATION_ROOT" \
  || die "Could not apply the reviewed localization overlay"
python3 "$SOURCE_ROOT/tools/audit_locales.py" \
  || die "Localization structural audit failed after applying the overlay"
python3 "$SOURCE_ROOT/tools/review_locales.py" \
  || die "Localization editorial audit failed after applying the overlay"

# Validate the exact generated root installer before any server mutation.
GENERATED_CHECK="$WORK_DIR/install.generated.sh"
HP_HARDENER_SOURCE_ROOT="$CHECKOUT" \
python3 "$SOURCE_ROOT/tools/harden_install.py" \
  "$SOURCE_ROOT/install.base.sh" "$GENERATED_CHECK" \
  || die "Could not derive the hardened installer during bootstrap validation"
bash -n "$SOURCE_ROOT/install.sh" \
  || die "Hardened installer launcher has invalid Bash syntax"
bash -n "$GENERATED_CHECK" \
  || die "Derived hardened installer has invalid Bash syntax"
rm -f "$GENERATED_CHECK"

REINSTALL_REQUESTED=no
for argument in "$@"; do
  [[ "$argument" == --reinstall ]] && REINSTALL_REQUESTED=yes
done
if [[ "$REINSTALL_REQUESTED" == yes ]]; then
  say "Reconciling preserved local PostgreSQL state"
  repair_preserved_local_postgres
fi

say "Starting the verified hardened HostPanel installer"
set +e
bash "$SOURCE_ROOT/install.sh" "$@" &
INSTALLER_PID=$!
wait "$INSTALLER_PID"
status=$?
INSTALLER_PID=""
set -e
if ((status != 0)) && [[ -r /var/log/hostpanel-installer/install.log ]]; then
  printf '\n--- Final HostPanel installer log lines ---\n' >&2
  tail -n 120 /var/log/hostpanel-installer/install.log >&2 || true
  printf '%s\n' '--- end installer log ---' >&2
fi
exit "$status"
