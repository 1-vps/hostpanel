#!/usr/bin/env bash
# Fetch a reviewed HostPanel commit, verify its embedded signed source archive,
# and run that commit's current installer from the complete extracted source tree.
set -euo pipefail

REPO="${HP_REPO:-https://github.com/1-vps/hostpanel.git}"
REF="${HP_REPO_REF:-}"
WORK_DIR=""
PG_URL_FILE=""

say(){ printf '\n==> %s\n' "$*"; }
die(){ printf '\nError: %s\n' "$*" >&2; exit 1; }
cleanup(){
  [[ -z "$PG_URL_FILE" ]] || rm -f -- "$PG_URL_FILE"
  [[ -z "$WORK_DIR" ]] || rm -rf -- "$WORK_DIR"
}
trap cleanup EXIT

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
            cursor.execute(
                sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(role_name))
            )
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
  if [[ "$result" == repaired ]]; then
    printf '  ok preserved local PostgreSQL role, database and password reconciled\n'
  fi
}

[[ "$REPO" =~ ^https://[^[:space:]]+$ ]] \
  || die "HP_REPO must be an HTTPS Git repository URL"
[[ "$REF" =~ ^[0-9a-fA-F]{40}$ ]] \
  || die "Set HP_REPO_REF to the reviewed full 40-character Git commit SHA"

for command in git sha256sum openssl python3 mktemp; do
  command -v "$command" >/dev/null 2>&1 \
    || die "$command is required before running the bootstrap"
done

WORK_DIR="$(mktemp -d /tmp/hostpanel-bootstrap.XXXXXX)" \
  || die "Could not create a private bootstrap directory"
chmod 700 "$WORK_DIR"
CHECKOUT="$WORK_DIR/repository"
EXTRACT_ROOT="$WORK_DIR/release"
mkdir -p "$CHECKOUT" "$EXTRACT_ROOT"

say "Fetching reviewed HostPanel commit $REF"
git -C "$CHECKOUT" init -q \
  || die "Could not initialise the temporary Git checkout"
git -C "$CHECKOUT" remote add origin "$REPO" \
  || die "Could not configure the reviewed Git repository"
git -C "$CHECKOUT" fetch --depth 1 origin "$REF" \
  || die "Could not fetch reviewed commit $REF from $REPO"
FETCHED_COMMIT="$(git -C "$CHECKOUT" rev-parse --verify 'FETCH_HEAD^{commit}')" \
  || die "Could not resolve the fetched Git commit"
[[ "${FETCHED_COMMIT,,}" == "${REF,,}" ]] \
  || die "Fetched Git commit does not match HP_REPO_REF"
git -C "$CHECKOUT" checkout -q --detach "$FETCHED_COMMIT" \
  || die "Could not check out reviewed commit $REF"

CHECKSUMS="$CHECKOUT/SHA256SUMS"
[[ -s "$CHECKSUMS" ]] \
  || die "Reviewed commit is missing SHA256SUMS"
mapfile -t SOURCE_ARCHIVES < <(
  awk '
    $2 ~ /^hostpanel-v[0-9]+\.[0-9]+\.[0-9]+([-+][0-9A-Za-z][0-9A-Za-z.-]*)?-source\.tar\.gz$/ {
      print $2
    }
  ' "$CHECKSUMS"
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
PUBLIC_KEY="$CHECKOUT/hostpanel-v${ARCHIVE_RELEASE_ID}-release.pub"
[[ -s "$ARCHIVE" && -s "$SIGNATURE" && -s "$PUBLIC_KEY" ]] \
  || die "Reviewed commit lacks the complete signed source-release files"

say "Verifying source archive checksum and signature"
CHECKSUM_LINE="$(awk -v name="$ARCHIVE_NAME" '
  $2 == name {line=$0; count++}
  END {if (count != 1) exit 1; print line}
' "$CHECKSUMS")" \
  || die "SHA256SUMS must contain exactly one entry for $ARCHIVE_NAME"
printf '%s\n' "$CHECKSUM_LINE" \
  | (cd "$CHECKOUT" && sha256sum -c -) \
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
with tarfile.open(archive, "r:gz") as handle:
    members = handle.getmembers()
    for member in members:
        path = pathlib.PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts:
            raise SystemExit(f"unsafe archive path: {member.name}")
        if member.issym() or member.islnk() or member.isdev() or member.isfifo():
            raise SystemExit(f"unsupported archive member: {member.name}")
    extract_options = {}
    if "filter" in inspect.signature(handle.extractall).parameters:
        extract_options["filter"] = "fully_trusted"
    handle.extractall(destination, members=members, **extract_options)
PYARCHIVE

mapfile -t RELEASE_ROOTS < <(find "$EXTRACT_ROOT" -mindepth 1 -maxdepth 1 -type d -print)
((${#RELEASE_ROOTS[@]} == 1)) \
  || die "Source archive must contain exactly one top-level directory"
SOURCE_ROOT="${RELEASE_ROOTS[0]}"
EXPECTED_ROOT_NAME="${ARCHIVE_NAME%-source.tar.gz}"
[[ "${SOURCE_ROOT##*/}" == "$EXPECTED_ROOT_NAME" ]] \
  || die "Source archive top-level directory does not match its signed filename"
[[ -d "$SOURCE_ROOT/app" ]] \
  || die "Verified source archive is missing app/"
[[ -s "$SOURCE_ROOT/VERSION" ]] \
  || die "Verified source archive is missing VERSION"
[[ -s "$SOURCE_ROOT/requirements.lock" ]] \
  || die "Verified source archive is missing requirements.lock"
SOURCE_VERSION="$(tr -d '[:space:]' <"$SOURCE_ROOT/VERSION")"
[[ "$SOURCE_VERSION" =~ ^[0-9]+(\.[0-9]+){2}([-+][0-9A-Za-z][0-9A-Za-z.-]*)?$ ]] \
  || die "Verified source archive contains an invalid VERSION"
ARCHIVE_VERSION_CORE="${ARCHIVE_RELEASE_ID%%[-+]*}"
SOURCE_VERSION_CORE="${SOURCE_VERSION%%[-+]*}"
[[ "$SOURCE_VERSION_CORE" == "$ARCHIVE_VERSION_CORE" ]] \
  || die "Verified source archive VERSION is incompatible with its signed filename"

# The signed archive may contain an older installer hotfix level. Preserve its
# complete application tree while running reviewed hotfixes from the same pinned
# Git commit.
install -m 0755 "$CHECKOUT/install.sh" "$SOURCE_ROOT/install.sh"
INSTALLER_HOTFIX="$CHECKOUT/release-hotfixes/install/php9_probe.py"
[[ -f "$INSTALLER_HOTFIX" && ! -L "$INSTALLER_HOTFIX" ]] \
  || die "Reviewed commit is missing the PHP 9 installer probe hotfix"
[[ -f "$SOURCE_ROOT/install.sh" && ! -L "$SOURCE_ROOT/install.sh" ]] \
  || die "Verified source release contains an unsafe installer path"
python3 "$INSTALLER_HOTFIX" "$SOURCE_ROOT/install.sh" \
  || die "Could not apply the reviewed PHP 9 installer probe hotfix"
bash -n "$SOURCE_ROOT/install.sh" \
  || die "PHP 9 installer probe produced invalid Bash"
DBCOMPAT_HOTFIX="$CHECKOUT/release-hotfixes/app/dbcompat.py"
[[ -f "$DBCOMPAT_HOTFIX" && ! -L "$DBCOMPAT_HOTFIX" ]] \
  || die "Reviewed commit is missing the PostgreSQL schema-order hotfix"
[[ -f "$SOURCE_ROOT/app/dbcompat.py" && ! -L "$SOURCE_ROOT/app/dbcompat.py" ]] \
  || die "Verified source archive contains an unsafe dbcompat.py path"
python3 - "$DBCOMPAT_HOTFIX" <<'PYHOTFIX'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
compile(path.read_text(encoding="utf-8"), str(path), "exec")
PYHOTFIX
install -m 0644 "$DBCOMPAT_HOTFIX" "$SOURCE_ROOT/app/dbcompat.py"

REINSTALL_REQUESTED=no
for argument in "$@"; do
  [[ "$argument" == --reinstall ]] && REINSTALL_REQUESTED=yes
done
if [[ "$REINSTALL_REQUESTED" == yes ]]; then
  say "Reconciling preserved local PostgreSQL state"
  repair_preserved_local_postgres
fi

say "Starting HostPanel installer from the complete verified source tree"
set +e
bash "$SOURCE_ROOT/install.sh" "$@"
status=$?
set -e
if ((status != 0)) && [[ -r /var/log/hostpanel-install.log ]]; then
  printf '\n--- Final HostPanel installer log lines ---\n' >&2
  tail -n 120 /var/log/hostpanel-install.log >&2 || true
  printf '%s\n' '--- end installer log ---' >&2
fi
exit "$status"
