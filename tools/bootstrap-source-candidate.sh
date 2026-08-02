#!/usr/bin/env bash
# Reproduce the reviewed unsigned source candidate from its signed baseline,
# verify the generated archive and attestations, and install that exact tree.
set -Eeuo pipefail

REPO="${HP_REPO:-https://github.com/1-vps/hostpanel.git}"
REF="${HP_REPO_REF:-}"
WORK_DIR=""

say(){ printf '\n==> %s\n' "$*"; }
die(){ printf '\nError: %s\n' "$*" >&2; exit 1; }
cleanup(){
  [[ -z "$WORK_DIR" ]] || rm -rf -- "$WORK_DIR"
}
trap cleanup EXIT HUP INT TERM

[[ "$REPO" =~ ^https://[^[:space:]]+$ ]] \
  || die "HP_REPO must be an HTTPS Git repository URL"
[[ "$REF" =~ ^[0-9a-fA-F]{40}$ ]] \
  || die "Set HP_REPO_REF to the reviewed full 40-character Git commit SHA"
REF="${REF,,}"

for command in git python3 openssl sha256sum tar mktemp; do
  command -v "$command" >/dev/null 2>&1 \
    || die "$command is required before reproducing the source candidate"
done

WORK_DIR="$(mktemp -d /tmp/hostpanel-source-candidate.XXXXXX)" \
  || die "Could not create a private source-candidate directory"
chmod 700 "$WORK_DIR"
CHECKOUT="$WORK_DIR/repository"
DIST="$WORK_DIR/dist"
EXTRACT_ROOT="$WORK_DIR/extracted"
mkdir -m 700 "$CHECKOUT" "$DIST" "$EXTRACT_ROOT"

say "Fetching reviewed HostPanel commit $REF"
git -C "$CHECKOUT" init -q \
  || die "Could not initialise the reviewed checkout"
git -C "$CHECKOUT" remote add origin "$REPO" \
  || die "Could not configure the reviewed repository"
git -C "$CHECKOUT" fetch --depth 1 origin "$REF" \
  || die "Could not fetch reviewed commit $REF"
FETCHED_COMMIT="$(git -C "$CHECKOUT" rev-parse --verify 'FETCH_HEAD^{commit}')" \
  || die "Could not resolve the fetched commit"
[[ "${FETCHED_COMMIT,,}" == "$REF" ]] \
  || die "Fetched commit does not match HP_REPO_REF"
git -C "$CHECKOUT" checkout -q --detach "$FETCHED_COMMIT" \
  || die "Could not check out reviewed commit $REF"
[[ "$(git -C "$CHECKOUT" rev-parse HEAD)" == "$REF" ]] \
  || die "Reviewed checkout moved from the requested commit"
git -C "$CHECKOUT" diff --exit-code -- . \
  || die "Reviewed checkout is not clean"

for required in \
  SOURCE_VERSION RELEASE_VERSION SHA256SUMS \
  tools/run-source-release-builder.py \
  tools/build-source-release.py \
  tools/generate-source-attestations.py \
  releases/source-release-policy.json \
  releases/update.pub; do
  [[ -f "$CHECKOUT/$required" && ! -L "$CHECKOUT/$required" ]] \
    || die "Reviewed commit contains an unsafe or missing candidate input: $required"
done

say "Reproducing exact source candidate from signed baseline"
PYTHONDONTWRITEBYTECODE=1 python3 "$CHECKOUT/tools/run-source-release-builder.py" \
  --repository-root "$CHECKOUT" \
  --commit "$REF" \
  --output-dir "$DIST" \
  || die "Source candidate reproduction failed"

mapfile -t SOURCE_ARCHIVES < <(
  find "$DIST" -maxdepth 1 -type f \
    -name 'hostpanel-v*-source.tar.gz' -printf '%f\n' | sort
)
((${#SOURCE_ARCHIVES[@]} == 1)) \
  || die "Candidate output must contain exactly one source archive"
ARCHIVE_NAME="${SOURCE_ARCHIVES[0]}"
ARCHIVE="$DIST/$ARCHIVE_NAME"

for required in \
  "$ARCHIVE" \
  "$DIST/source-build.json" \
  "$DIST/sbom.cdx.json" \
  "$DIST/dependency-lock-attestation.json" \
  "$DIST/source-file-manifest.json"; do
  [[ -f "$required" && ! -L "$required" && -s "$required" ]] \
    || die "Candidate output is missing a required regular file: ${required##*/}"
done

python3 - "$DIST" "$REF" "$CHECKOUT/SOURCE_VERSION" "$CHECKOUT/RELEASE_VERSION" <<'PYVERIFY'
import hashlib
import json
import pathlib
import re
import stat
import sys

output = pathlib.Path(sys.argv[1])
commit = sys.argv[2]
source_version_path = pathlib.Path(sys.argv[3])
release_version_path = pathlib.Path(sys.argv[4])
semver = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)

def clean_line(path: pathlib.Path) -> str:
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise SystemExit(f"unsafe version file: {path.name}")
    raw = path.read_text(encoding="utf-8", errors="strict")
    lines = raw.splitlines()
    if len(lines) != 1 or raw not in {lines[0], lines[0] + "\n"}:
        raise SystemExit(f"non-canonical version file: {path.name}")
    value = lines[0]
    if not value or value.strip() != value or semver.fullmatch(value) is None:
        raise SystemExit(f"invalid version file: {path.name}")
    return value

def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

source_version = clean_line(source_version_path)
release_id = clean_line(release_version_path)
if source_version.split("-", 1)[0].split("+", 1)[0] != release_id.split("-", 1)[0].split("+", 1)[0]:
    raise SystemExit("SOURCE_VERSION and RELEASE_VERSION core versions differ")

provenance = json.loads((output / "source-build.json").read_text(encoding="utf-8"))
if provenance.get("commit") != commit:
    raise SystemExit("source-build.json is not bound to the reviewed commit")
if provenance.get("source_version") != source_version:
    raise SystemExit("source-build.json source version mismatch")
if provenance.get("release_id") != release_id:
    raise SystemExit("source-build.json release identifier mismatch")
archive = provenance.get("archive")
if not isinstance(archive, dict):
    raise SystemExit("source-build.json archive entry is invalid")
archive_path = output / str(archive.get("name", ""))
if not archive_path.is_file() or archive.get("sha256") != sha256(archive_path):
    raise SystemExit("source archive digest does not match provenance")

for name in (
    "sbom.cdx.json",
    "dependency-lock-attestation.json",
    "source-file-manifest.json",
):
    payload = json.loads((output / name).read_text(encoding="utf-8"))
    if name != "sbom.cdx.json":
        if payload.get("source_version") != source_version or payload.get("release_id") != release_id:
            raise SystemExit(f"{name} identity mismatch")
PYVERIFY

say "Safely extracting reproduced source candidate"
python3 - "$ARCHIVE" "$EXTRACT_ROOT" <<'PYEXTRACT'
import os
import pathlib
import shutil
import sys
import tarfile

archive_path = pathlib.Path(sys.argv[1])
destination = pathlib.Path(sys.argv[2])
max_members = 50_000
max_total_size = 2 * 1024 * 1024 * 1024
seen = set()
roots = set()
total_size = 0

with tarfile.open(archive_path, "r:gz") as archive:
    members = archive.getmembers()
    if not members or len(members) > max_members:
        raise SystemExit("candidate archive is empty or contains too many members")
    checked = []
    for member in members:
        path = pathlib.PurePosixPath(member.name.rstrip("/"))
        if not path.parts or path.is_absolute() or ".." in path.parts:
            raise SystemExit(f"unsafe candidate archive path: {member.name}")
        normalized = path.as_posix()
        if normalized in seen:
            raise SystemExit(f"duplicate candidate archive path: {normalized}")
        seen.add(normalized)
        roots.add(path.parts[0])
        if member.issym() or member.islnk() or member.isdev() or member.isfifo():
            raise SystemExit(f"unsupported candidate archive member: {member.name}")
        if not (member.isdir() or member.isfile()):
            raise SystemExit(f"unsupported candidate archive type: {member.name}")
        total_size += max(member.size, 0)
        if total_size > max_total_size:
            raise SystemExit("candidate archive expands beyond the safety limit")
        checked.append((member, path))
    if len(roots) != 1:
        raise SystemExit("candidate archive must contain exactly one top-level root")

    for member, path in checked:
        target = destination.joinpath(*path.parts)
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
            os.chmod(target, 0o755)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() or target.is_symlink():
            raise SystemExit(f"candidate extraction target already exists: {path}")
        source = archive.extractfile(member)
        if source is None:
            raise SystemExit(f"could not read candidate member: {member.name}")
        with source, target.open("xb") as output:
            shutil.copyfileobj(source, output, length=1024 * 1024)
        os.chmod(target, 0o755 if member.mode & 0o111 else 0o644)
PYEXTRACT

mapfile -t SOURCE_ROOTS < <(find "$EXTRACT_ROOT" -mindepth 1 -maxdepth 1 -type d -print)
((${#SOURCE_ROOTS[@]} == 1)) \
  || die "Extracted candidate must contain exactly one top-level directory"
SOURCE_ROOT="${SOURCE_ROOTS[0]}"
EXPECTED_VERSION="$(tr -d '[:space:]' <"$CHECKOUT/SOURCE_VERSION")"
INSTALLED_VERSION="$(tr -d '[:space:]' <"$SOURCE_ROOT/VERSION")"
[[ "$INSTALLED_VERSION" == "$EXPECTED_VERSION" ]] \
  || die "Extracted candidate VERSION does not match SOURCE_VERSION"
[[ -x "$SOURCE_ROOT/install.sh" ]] \
  || die "Extracted source candidate lacks an executable installer"

say "Installing reproduced HostPanel source candidate $EXPECTED_VERSION"
cd "$SOURCE_ROOT"
bash "$SOURCE_ROOT/install.sh" "$@"
