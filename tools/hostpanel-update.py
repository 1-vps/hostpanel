#!/usr/bin/env python3
"""Check and apply signed HostPanel releases published through GitHub Releases."""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
import pathlib
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import BinaryIO, Iterable

SEMVER_RE = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
ASSET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,199}$")

DEFAULT_CONFIG_FILES = (
    pathlib.Path("/opt/hostpanel/config.env"),
    pathlib.Path("/etc/hostpanel/update-agent.conf"),
)
DEFAULT_PUBLIC_KEY = pathlib.Path("/etc/hostpanel/update.pub")
DEFAULT_TOKEN_FILE = pathlib.Path("/etc/hostpanel/github-update.token")
DEFAULT_STATUS_FILE = pathlib.Path("/var/lib/hostpanel/update-status.json")
DEFAULT_LOCK_FILE = pathlib.Path("/run/hostpanel-update.lock")
DEFAULT_VERSION_FILE = pathlib.Path("/opt/hostpanel/VERSION")

MAX_MANIFEST_BYTES = 1024 * 1024
MAX_SIGNATURE_BYTES = 64 * 1024
MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 50000
MAX_EXTRACTED_BYTES = 2 * 1024 * 1024 * 1024


class UpdateError(RuntimeError):
    """A user-facing update failure."""


@dataclass(frozen=True)
class Version:
    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...] = ()

    @classmethod
    def parse(cls, value: str) -> "Version":
        match = SEMVER_RE.fullmatch(value.strip())
        if match is None:
            raise UpdateError(f"invalid semantic version: {value!r}")
        prerelease = tuple((match.group(4) or "").split(".")) if match.group(4) else ()
        return cls(int(match.group(1)), int(match.group(2)), int(match.group(3)), prerelease)

    def __lt__(self, other: "Version") -> bool:
        left = (self.major, self.minor, self.patch)
        right = (other.major, other.minor, other.patch)
        if left != right:
            return left < right
        if not self.prerelease:
            return False
        if not other.prerelease:
            return True
        for a, b in zip(self.prerelease, other.prerelease):
            if a == b:
                continue
            a_num = a.isdigit()
            b_num = b.isdigit()
            if a_num and b_num:
                return int(a) < int(b)
            if a_num != b_num:
                return a_num
            return a < b
        return len(self.prerelease) < len(other.prerelease)


@dataclass(frozen=True)
class ReleaseManifest:
    version: str
    channel: str
    commit: str
    tag: str
    archive_name: str
    archive_sha256: str
    archive_signature_name: str

    @classmethod
    def from_bytes(cls, payload: bytes) -> "ReleaseManifest":
        try:
            data = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise UpdateError(f"invalid update manifest: {exc}") from exc
        if not isinstance(data, dict) or set(data) != {
            "schema",
            "product",
            "channel",
            "version",
            "commit",
            "tag",
            "archive",
        }:
            raise UpdateError("update manifest has an unexpected shape")
        if data["schema"] != 1 or data["product"] != "hostpanel":
            raise UpdateError("update manifest schema or product is unsupported")
        Version.parse(str(data["version"]))
        channel = str(data["channel"])
        if channel not in {"stable", "beta"}:
            raise UpdateError("update manifest channel is unsupported")
        commit = str(data["commit"]).lower()
        if COMMIT_RE.fullmatch(commit) is None:
            raise UpdateError("update manifest commit is invalid")
        tag = str(data["tag"])
        if tag != f"v{data['version']}":
            raise UpdateError("update manifest tag does not match version")
        archive = data["archive"]
        if not isinstance(archive, dict) or set(archive) != {"name", "sha256", "signature"}:
            raise UpdateError("update manifest archive entry is invalid")
        name = str(archive["name"])
        signature = str(archive["signature"])
        digest = str(archive["sha256"]).lower()
        if ASSET_RE.fullmatch(name) is None or ASSET_RE.fullmatch(signature) is None:
            raise UpdateError("update manifest contains an unsafe asset name")
        if signature != f"{name}.sig":
            raise UpdateError("update archive signature name is inconsistent")
        if SHA256_RE.fullmatch(digest) is None:
            raise UpdateError("update archive SHA-256 is invalid")
        return cls(
            version=str(data["version"]),
            channel=channel,
            commit=commit,
            tag=tag,
            archive_name=name,
            archive_sha256=digest,
            archive_signature_name=signature,
        )


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follow redirects without forwarding GitHub credentials cross-origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is None:
            return None
        old = urllib.parse.urlsplit(req.full_url)
        new = urllib.parse.urlsplit(newurl)
        if (old.scheme, old.hostname, old.port) != (new.scheme, new.hostname, new.port):
            redirected.headers.pop("Authorization", None)
            redirected.unredirected_hdrs.pop("Authorization", None)
        return redirected


def load_config(paths: Iterable[pathlib.Path]) -> dict[str, str]:
    result: dict[str, str] = {}
    key_re = re.compile(r"^[A-Z][A-Z0-9_]*$")
    for path in paths:
        if not path.is_file() or path.is_symlink():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key_re.fullmatch(key) is None:
                continue
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            result[key] = value
    return result


def atomic_json(path: pathlib.Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}")
    temporary.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def record_status(path: pathlib.Path, *, state: str, message: str, **extra: object) -> None:
    payload: dict[str, object] = {
        "state": state,
        "message": message,
        "checked_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
    }
    payload.update(extra)
    atomic_json(path, payload)


def read_regular_file(path: pathlib.Path, *, maximum: int, require_root_owner: bool = False) -> bytes:
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise UpdateError(f"unsafe file: {path}")
        if require_root_owner and metadata.st_uid != 0:
            raise UpdateError(f"file must be root-owned: {path}")
        if metadata.st_size > maximum:
            raise UpdateError(f"file exceeds size limit: {path}")
        with os.fdopen(os.dup(fd), "rb") as handle:
            data = handle.read(maximum + 1)
        if len(data) > maximum:
            raise UpdateError(f"file exceeds size limit: {path}")
        return data
    finally:
        os.close(fd)


def token_from_file(path: pathlib.Path) -> str | None:
    if not path.exists():
        return None
    data = read_regular_file(path, maximum=8192, require_root_owner=(os.geteuid() == 0))
    token = data.decode("utf-8", errors="strict").strip()
    if not token or any(ord(character) < 33 for character in token):
        raise UpdateError(f"invalid GitHub token file: {path}")
    return token


def build_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(SafeRedirectHandler())


def github_request(
    opener: urllib.request.OpenerDirector,
    url: str,
    *,
    token: str | None,
    accept: str,
):
    if urllib.parse.urlsplit(url).scheme != "https":
        raise UpdateError("update URLs must use HTTPS")
    headers = {
        "Accept": accept,
        "User-Agent": "HostPanel-Updater/1",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    try:
        return opener.open(request, timeout=30)
    except urllib.error.HTTPError as exc:
        raise UpdateError(f"GitHub request failed with HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise UpdateError(f"GitHub request failed: {exc.reason}") from exc


def read_response(response: BinaryIO, *, maximum: int) -> bytes:
    data = response.read(maximum + 1)
    if len(data) > maximum:
        raise UpdateError("download exceeds the configured size limit")
    return data


def fetch_latest_release(
    opener: urllib.request.OpenerDirector, repository: str, token: str | None
) -> dict[str, object]:
    if REPOSITORY_RE.fullmatch(repository) is None:
        raise UpdateError("HP_UPDATE_REPOSITORY must be owner/repository")
    url = f"https://api.github.com/repos/{repository}/releases/latest"
    with github_request(opener, url, token=token, accept="application/vnd.github+json") as response:
        payload = read_response(response, maximum=2 * 1024 * 1024)
    try:
        release = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateError(f"GitHub returned an invalid release document: {exc}") from exc
    if not isinstance(release, dict) or release.get("draft") is not False:
        raise UpdateError("latest GitHub release is missing or is still a draft")
    return release


def asset_map(release: dict[str, object]) -> dict[str, dict[str, object]]:
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise UpdateError("GitHub release does not contain an asset list")
    mapped: dict[str, dict[str, object]] = {}
    for item in assets:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        url = item.get("url")
        size = item.get("size")
        if isinstance(name, str) and isinstance(url, str) and isinstance(size, int):
            mapped[name] = item
    return mapped


def download_asset(
    opener: urllib.request.OpenerDirector,
    asset: dict[str, object],
    destination: pathlib.Path,
    *,
    token: str | None,
    maximum: int,
) -> None:
    url = asset.get("url")
    advertised_size = asset.get("size")
    if not isinstance(url, str) or not isinstance(advertised_size, int):
        raise UpdateError("GitHub release asset metadata is invalid")
    if advertised_size < 0 or advertised_size > maximum:
        raise UpdateError("GitHub release asset exceeds the configured size limit")
    with github_request(opener, url, token=token, accept="application/octet-stream") as response:
        with destination.open("xb") as output:
            total = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > maximum:
                    raise UpdateError("GitHub release asset exceeds the configured size limit")
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
    os.chmod(destination, 0o600)


def verify_signature(public_key: pathlib.Path, payload: pathlib.Path, signature: pathlib.Path) -> None:
    read_regular_file(public_key, maximum=64 * 1024, require_root_owner=(os.geteuid() == 0))
    read_regular_file(signature, maximum=MAX_SIGNATURE_BYTES)
    completed = subprocess.run(
        [
            "openssl",
            "pkeyutl",
            "-verify",
            "-pubin",
            "-inkey",
            str(public_key),
            "-rawin",
            "-in",
            str(payload),
            "-sigfile",
            str(signature),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise UpdateError("release signature verification failed")


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_extract(archive_path: pathlib.Path, destination: pathlib.Path, expected_root: str) -> pathlib.Path:
    total = 0
    root_parts: set[str] = set()
    with tarfile.open(archive_path, "r:gz") as archive:
        members = archive.getmembers()
        if not members or len(members) > MAX_ARCHIVE_MEMBERS:
            raise UpdateError("release archive member count is unsafe")
        for member in members:
            path = pathlib.PurePosixPath(member.name)
            if not path.parts or path.is_absolute() or ".." in path.parts:
                raise UpdateError(f"unsafe release archive path: {member.name}")
            root_parts.add(path.parts[0])
            if member.issym() or member.islnk() or member.isdev() or member.isfifo():
                raise UpdateError(f"unsupported release archive member: {member.name}")
            if not (member.isdir() or member.isfile()):
                raise UpdateError(f"unsupported release archive member: {member.name}")
            if member.mode & 0o7022:
                raise UpdateError(f"unsafe release archive mode: {member.name}")
            total += max(member.size, 0)
            if total > MAX_EXTRACTED_BYTES:
                raise UpdateError("release archive expands beyond the safety limit")
        if root_parts != {expected_root}:
            raise UpdateError("release archive root does not match the signed version")
        for member in members:
            path = pathlib.PurePosixPath(member.name)
            target = destination.joinpath(*path.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True, mode=0o755)
                os.chmod(target, 0o755)
                continue
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            source = archive.extractfile(member)
            if source is None:
                raise UpdateError(f"could not read release archive member: {member.name}")
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
            fd = os.open(target, flags, 0o600)
            try:
                with source, os.fdopen(fd, "wb", closefd=False) as output:
                    shutil.copyfileobj(source, output)
                    output.flush()
                    os.fsync(output.fileno())
            finally:
                os.close(fd)
            os.chmod(target, 0o755 if member.mode & 0o111 else 0o644)
    root = destination / expected_root
    for required in ("VERSION", "install.sh", "install.base.sh", "tools/harden_install.py"):
        path = root / required
        if not path.is_file() or path.is_symlink():
            raise UpdateError(f"release archive is missing required file: {required}")
    return root


def current_version(path: pathlib.Path) -> str:
    data = read_regular_file(path, maximum=4096)
    value = data.decode("utf-8", errors="strict").strip()
    Version.parse(value)
    return value


def select_asset(assets: dict[str, dict[str, object]], name: str) -> dict[str, object]:
    item = assets.get(name)
    if item is None:
        raise UpdateError(f"GitHub release is missing asset: {name}")
    return item


def apply_release(source_root: pathlib.Path, manifest_url: str) -> None:
    environment = os.environ.copy()
    environment["HP_UPDATE_MANIFEST"] = manifest_url
    completed = subprocess.run(
        ["bash", str(source_root / "install.sh"), "--reinstall"],
        cwd=source_root,
        env=environment,
        check=False,
    )
    if completed.returncode != 0:
        raise UpdateError(f"HostPanel installer failed with exit status {completed.returncode}")


def run(args: argparse.Namespace) -> int:
    if os.geteuid() != 0 and not args.allow_non_root:
        raise UpdateError("HostPanel updates must run as root")
    config_paths = [pathlib.Path(item) for item in args.config]
    config = load_config(config_paths)
    repository = args.repository or config.get("HP_UPDATE_REPOSITORY", "1-vps/hostpanel")
    channel = config.get("HP_UPDATE_CHANNEL", "stable")
    token_file = pathlib.Path(config.get("HP_UPDATE_TOKEN_FILE", str(DEFAULT_TOKEN_FILE)))
    public_key = pathlib.Path(config.get("HP_UPDATE_PUBLIC_KEY", str(DEFAULT_PUBLIC_KEY)))
    auto_update = config.get("HP_AUTO_UPDATE", "yes").lower() == "yes"
    require_token = config.get("HP_UPDATE_REQUIRE_TOKEN", "yes").lower() == "yes"
    status_file = pathlib.Path(args.status_file)
    version_file = pathlib.Path(args.version_file)
    installed = current_version(version_file)
    token = token_from_file(token_file)
    if require_token and token is None:
        record_status(
            status_file,
            state="waiting",
            message=f"GitHub update token is not configured at {token_file}",
            installed_version=installed,
        )
        print(f"GitHub update token is not configured at {token_file}.")
        return 0
    opener = build_opener()
    release = fetch_latest_release(opener, repository, token)
    if channel == "stable" and release.get("prerelease") is True:
        raise UpdateError("latest GitHub release is a prerelease, but the stable channel is configured")
    assets = asset_map(release)
    manifest_name = "hostpanel-update-manifest.json"
    manifest_sig_name = f"{manifest_name}.sig"
    with tempfile.TemporaryDirectory(prefix="hostpanel-update.") as temporary_name:
        temporary = pathlib.Path(temporary_name)
        manifest_path = temporary / manifest_name
        manifest_sig_path = temporary / manifest_sig_name
        download_asset(
            opener,
            select_asset(assets, manifest_name),
            manifest_path,
            token=token,
            maximum=MAX_MANIFEST_BYTES,
        )
        download_asset(
            opener,
            select_asset(assets, manifest_sig_name),
            manifest_sig_path,
            token=token,
            maximum=MAX_SIGNATURE_BYTES,
        )
        verify_signature(public_key, manifest_path, manifest_sig_path)
        manifest = ReleaseManifest.from_bytes(
            read_regular_file(manifest_path, maximum=MAX_MANIFEST_BYTES)
        )
        if manifest.channel != channel:
            raise UpdateError(
                f"latest release channel {manifest.channel!r} does not match configured channel {channel!r}"
            )
        if release.get("tag_name") != manifest.tag:
            raise UpdateError("GitHub release tag does not match the signed manifest")
        if not Version.parse(installed) < Version.parse(manifest.version):
            record_status(
                status_file,
                state="current",
                message=f"HostPanel {installed} is current",
                installed_version=installed,
                available_version=manifest.version,
                commit=manifest.commit,
            )
            print(f"HostPanel {installed} is current.")
            return 0
        record_status(
            status_file,
            state="available",
            message=f"HostPanel {manifest.version} is available",
            installed_version=installed,
            available_version=manifest.version,
            commit=manifest.commit,
        )
        print(f"HostPanel {manifest.version} is available (installed: {installed}).")
        if args.check or not args.apply or (args.auto and not auto_update):
            return 10
        archive_path = temporary / manifest.archive_name
        archive_sig_path = temporary / manifest.archive_signature_name
        download_asset(
            opener,
            select_asset(assets, manifest.archive_name),
            archive_path,
            token=token,
            maximum=MAX_ARCHIVE_BYTES,
        )
        download_asset(
            opener,
            select_asset(assets, manifest.archive_signature_name),
            archive_sig_path,
            token=token,
            maximum=MAX_SIGNATURE_BYTES,
        )
        if sha256_file(archive_path) != manifest.archive_sha256:
            raise UpdateError("release archive SHA-256 does not match the signed manifest")
        verify_signature(public_key, archive_path, archive_sig_path)
        extraction = temporary / "source"
        extraction.mkdir(mode=0o700)
        expected_root = f"hostpanel-v{manifest.version}"
        source_root = safe_extract(archive_path, extraction, expected_root)
        archive_version = (source_root / "VERSION").read_text(encoding="utf-8").strip()
        if archive_version != manifest.version:
            raise UpdateError("release archive VERSION does not match the signed manifest")
        if args.dry_run:
            print("Signed update verified; dry-run requested, installation was not changed.")
            return 10
        manifest_url = f"https://api.github.com/repos/{repository}/releases/latest"
        apply_release(source_root, manifest_url)
        updated = current_version(version_file)
        if updated != manifest.version:
            raise UpdateError(
                f"installer completed but VERSION is {updated!r}, expected {manifest.version!r}"
            )
        record_status(
            status_file,
            state="updated",
            message=f"HostPanel updated to {manifest.version}",
            installed_version=updated,
            available_version=manifest.version,
            commit=manifest.commit,
        )
        print(f"HostPanel updated to {manifest.version}.")
        return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="check only; exit 10 when an update exists")
    mode.add_argument("--apply", action="store_true", help="apply a newer signed release")
    parser.add_argument("--auto", action="store_true", help="honour HP_AUTO_UPDATE before applying")
    parser.add_argument("--dry-run", action="store_true", help="download and verify without installing")
    parser.add_argument("--repository", help="GitHub repository in owner/name form")
    parser.add_argument(
        "--config",
        action="append",
        default=[str(path) for path in DEFAULT_CONFIG_FILES],
        help="configuration file; may be repeated",
    )
    parser.add_argument("--status-file", default=str(DEFAULT_STATUS_FILE))
    parser.add_argument("--version-file", default=str(DEFAULT_VERSION_FILE))
    parser.add_argument("--lock-file", default=str(DEFAULT_LOCK_FILE))
    parser.add_argument("--allow-non-root", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    lock_path = pathlib.Path(args.lock_file)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Another HostPanel update is already running.", file=sys.stderr)
            return 75
        try:
            return run(args)
        except UpdateError as exc:
            with contextlib.suppress(Exception):
                record_status(
                    pathlib.Path(args.status_file),
                    state="error",
                    message=str(exc),
                )
            print(f"HostPanel update failed: {exc}", file=sys.stderr)
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
