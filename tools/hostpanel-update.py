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

CORE_SEMVER_RE = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
IDENTIFIER_RE = re.compile(r"^[0-9A-Za-z-]+$")
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
ASSET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,199}$")
KEY_FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")

DEFAULT_CONFIG_FILES = (pathlib.Path("/etc/hostpanel/update-agent.conf"),)
DEFAULT_PUBLIC_KEY = pathlib.Path("/etc/hostpanel/update.pub")
DEFAULT_KEYRING = pathlib.Path("/etc/hostpanel/update-keyring.json")
DEFAULT_TOKEN_FILE = pathlib.Path("/etc/hostpanel/github-update.token")
DEFAULT_STATUS_FILE = pathlib.Path("/var/lib/hostpanel/update-status.json")
DEFAULT_LOCK_FILE = pathlib.Path("/run/hostpanel-update.lock")
DEFAULT_VERSION_FILE = pathlib.Path("/opt/hostpanel/VERSION")

ALLOWED_CONFIG_KEYS = {
    "HP_UPDATE_REPOSITORY",
    "HP_UPDATE_CHANNEL",
    "HP_UPDATE_TOKEN_FILE",
    "HP_UPDATE_REQUIRE_TOKEN",
    "HP_UPDATE_PUBLIC_KEY",
    "HP_UPDATE_KEYRING",
    "HP_AUTO_UPDATE",
}
ALLOWED_GITHUB_HOSTS = {
    "api.github.com",
    "github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
}
MAX_VERSION_BYTES = 128
MAX_VERSION_IDENTIFIERS = 32
MAX_VERSION_IDENTIFIER_BYTES = 64
MAX_CONFIG_BYTES = 64 * 1024
MAX_KEYRING_BYTES = 64 * 1024
MAX_PUBLIC_KEY_BYTES = 64 * 1024
MAX_RELEASE_DOCUMENT_BYTES = 2 * 1024 * 1024
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_SIGNATURE_BYTES = 64 * 1024
MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 50000
MAX_EXTRACTED_BYTES = 2 * 1024 * 1024 * 1024
MAX_BETA_RELEASES = 20
MAX_TRUSTED_KEYS = 8


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
        if not isinstance(value, str) or not value or len(value.encode("utf-8")) > MAX_VERSION_BYTES:
            raise UpdateError(f"invalid semantic version: {value!r}")
        match = CORE_SEMVER_RE.fullmatch(value)
        if match is None:
            raise UpdateError(f"invalid semantic version: {value!r}")
        prerelease = tuple(match.group(4).split(".")) if match.group(4) else ()
        if len(prerelease) > MAX_VERSION_IDENTIFIERS:
            raise UpdateError("semantic version contains too many prerelease identifiers")
        for identifier in prerelease:
            if (
                not identifier
                or len(identifier.encode("ascii")) > MAX_VERSION_IDENTIFIER_BYTES
                or IDENTIFIER_RE.fullmatch(identifier) is None
            ):
                raise UpdateError("semantic version contains an invalid prerelease identifier")
            if identifier.isdigit() and len(identifier) > 1 and identifier.startswith("0"):
                raise UpdateError("numeric prerelease identifiers must not contain leading zeroes")
        return cls(
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
            prerelease,
        )

    @property
    def is_prerelease(self) -> bool:
        return bool(self.prerelease)

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
            a_numeric = a.isdigit()
            b_numeric = b.isdigit()
            if a_numeric and b_numeric:
                return (len(a), a) < (len(b), b)
            if a_numeric != b_numeric:
                return a_numeric
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
        version_text = str(data["version"])
        parsed_version = Version.parse(version_text)
        channel = str(data["channel"])
        if channel not in {"stable", "beta"}:
            raise UpdateError("update manifest channel is unsupported")
        if channel == "stable" and parsed_version.is_prerelease:
            raise UpdateError("stable manifests must contain a final semantic version")
        if channel == "beta" and not parsed_version.is_prerelease:
            raise UpdateError("beta manifests must contain a prerelease semantic version")
        commit = str(data["commit"]).lower()
        if COMMIT_RE.fullmatch(commit) is None:
            raise UpdateError("update manifest commit is invalid")
        tag = str(data["tag"])
        if tag != f"v{version_text}":
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
            version=version_text,
            channel=channel,
            commit=commit,
            tag=tag,
            archive_name=name,
            archive_sha256=digest,
            archive_signature_name=signature,
        )


@dataclass(frozen=True)
class TrustedKey:
    key_id: str
    path: pathlib.Path
    payload: bytes
    activate_from: Version
    retire_after: Version | None

    def accepts(self, version: Version) -> bool:
        if version < self.activate_from:
            return False
        if self.retire_after is not None and self.retire_after < version:
            return False
        return True


@dataclass(frozen=True)
class ResolvedRelease:
    release: dict[str, object]
    assets: dict[str, dict[str, object]]
    manifest: ReleaseManifest
    signing_key: TrustedKey
    manifest_url: str


def _owner_ids() -> tuple[int, int]:
    if os.geteuid() == 0:
        return 0, 0
    return os.getuid(), os.getgid()


def _path_exists_without_follow(path: pathlib.Path) -> bool:
    return os.path.lexists(path)


def _validate_direct_parent(path: pathlib.Path, owner_uid: int, owner_gid: int) -> None:
    try:
        metadata = path.parent.lstat()
    except FileNotFoundError as exc:
        raise UpdateError(f"parent directory is missing: {path.parent}") from exc
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise UpdateError(f"unsafe parent directory: {path.parent}")
    if metadata.st_uid != owner_uid or metadata.st_gid != owner_gid:
        raise UpdateError(f"parent directory has unsafe ownership: {path.parent}")
    if stat.S_IMODE(metadata.st_mode) & 0o022:
        raise UpdateError(f"parent directory is group/world writable: {path.parent}")


def read_regular_file(
    path: pathlib.Path,
    *,
    maximum: int,
    require_root_owner: bool = False,
    expected_mode: int | None = None,
    require_private_parent: bool = False,
) -> bytes:
    owner_uid, owner_gid = _owner_ids()
    if require_private_parent:
        _validate_direct_parent(path, owner_uid, owner_gid)
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise UpdateError(f"could not safely open file: {path}") from exc
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise UpdateError(f"unsafe file: {path}")
        if require_root_owner and (before.st_uid != owner_uid or before.st_gid != owner_gid):
            raise UpdateError(f"file has unsafe ownership: {path}")
        if expected_mode is not None and stat.S_IMODE(before.st_mode) != expected_mode:
            raise UpdateError(
                f"file must have mode {expected_mode:04o}: {path}"
            )
        if before.st_size < 0 or before.st_size > maximum:
            raise UpdateError(f"file exceeds size limit: {path}")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, min(1024 * 1024, maximum + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum:
                raise UpdateError(f"file exceeds size limit: {path}")
        after = os.fstat(fd)
        stable_fields = (
            "st_dev",
            "st_ino",
            "st_uid",
            "st_gid",
            "st_mode",
            "st_nlink",
            "st_size",
            "st_mtime_ns",
        )
        if any(getattr(before, field) != getattr(after, field) for field in stable_fields):
            raise UpdateError(f"file changed while it was read: {path}")
        try:
            pathname = path.lstat()
        except FileNotFoundError as exc:
            raise UpdateError(f"file path changed while it was read: {path}") from exc
        if (pathname.st_dev, pathname.st_ino) != (after.st_dev, after.st_ino):
            raise UpdateError(f"file path changed while it was read: {path}")
        return b"".join(chunks)
    finally:
        os.close(fd)


def load_config(paths: Iterable[pathlib.Path]) -> dict[str, str]:
    result: dict[str, str] = {}
    key_re = re.compile(r"^[A-Z][A-Z0-9_]*$")
    for path in paths:
        if not _path_exists_without_follow(path):
            continue
        payload = read_regular_file(
            path,
            maximum=MAX_CONFIG_BYTES,
            require_root_owner=True,
            expected_mode=0o600,
            require_private_parent=True,
        )
        try:
            lines = payload.decode("utf-8").splitlines()
        except UnicodeDecodeError as exc:
            raise UpdateError(f"configuration is not valid UTF-8: {path}") from exc
        seen_in_file: set[str] = set()
        for number, raw in enumerate(lines, start=1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                raise UpdateError(f"invalid configuration line {path}:{number}")
            key, value = line.split("=", 1)
            if key_re.fullmatch(key) is None or key not in ALLOWED_CONFIG_KEYS:
                raise UpdateError(f"unsupported configuration key {key!r} in {path}")
            if key in seen_in_file or key in result:
                raise UpdateError(f"duplicate configuration key {key!r}")
            seen_in_file.add(key)
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            if not value or len(value.encode("utf-8")) > 4096:
                raise UpdateError(f"invalid value for configuration key {key!r}")
            if any(ord(character) < 32 or ord(character) == 127 for character in value):
                raise UpdateError(f"control character in configuration key {key!r}")
            result[key] = value
    return result


def token_from_file(path: pathlib.Path) -> str | None:
    if not _path_exists_without_follow(path):
        return None
    data = read_regular_file(
        path,
        maximum=8192,
        require_root_owner=True,
        expected_mode=0o600,
        require_private_parent=True,
    )
    try:
        token = data.decode("ascii")
    except UnicodeDecodeError as exc:
        raise UpdateError(f"GitHub token must contain ASCII bytes only: {path}") from exc
    if not token or any(character.isspace() or ord(character) < 33 or ord(character) > 126 for character in token):
        raise UpdateError(f"invalid GitHub token file: {path}")
    return token


def _parse_keyring_version(value: object, label: str) -> Version:
    if not isinstance(value, str):
        raise UpdateError(f"keyring {label} must be a semantic version")
    return Version.parse(value)


def load_keyring(path: pathlib.Path) -> tuple[TrustedKey, ...]:
    payload = read_regular_file(
        path,
        maximum=MAX_KEYRING_BYTES,
        require_root_owner=True,
        expected_mode=0o600,
        require_private_parent=True,
    )
    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateError(f"invalid update keyring: {exc}") from exc
    if not isinstance(data, dict) or set(data) != {"schema", "keys"} or data["schema"] != 1:
        raise UpdateError("update keyring has an unexpected shape")
    entries = data["keys"]
    if not isinstance(entries, list) or not entries or len(entries) > MAX_TRUSTED_KEYS:
        raise UpdateError("update keyring contains an unsafe key count")
    keys: list[TrustedKey] = []
    ids: set[str] = set()
    files: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {
            "id",
            "file",
            "activate_from",
            "retire_after",
        }:
            raise UpdateError("update keyring entry has an unexpected shape")
        key_id = str(entry["id"])
        file_name = str(entry["file"])
        if not key_id.startswith("sha256:") or SHA256_RE.fullmatch(key_id[7:]) is None:
            raise UpdateError("update keyring key ID is invalid")
        if KEY_FILE_RE.fullmatch(file_name) is None:
            raise UpdateError("update keyring key filename is unsafe")
        if key_id in ids or file_name in files:
            raise UpdateError("update keyring contains duplicate keys")
        ids.add(key_id)
        files.add(file_name)
        activate_from = _parse_keyring_version(entry["activate_from"], "activate_from")
        retire_raw = entry["retire_after"]
        retire_after = (
            None
            if retire_raw is None
            else _parse_keyring_version(retire_raw, "retire_after")
        )
        if retire_after is not None and retire_after < activate_from:
            raise UpdateError("update keyring retirement precedes activation")
        key_path = path.parent / file_name
        key_payload = read_regular_file(
            key_path,
            maximum=MAX_PUBLIC_KEY_BYTES,
            require_root_owner=True,
            expected_mode=0o644,
            require_private_parent=True,
        )
        actual_id = f"sha256:{hashlib.sha256(key_payload).hexdigest()}"
        if actual_id != key_id:
            raise UpdateError(f"update key ID does not match {file_name}")
        keys.append(
            TrustedKey(
                key_id=key_id,
                path=key_path,
                payload=key_payload,
                activate_from=activate_from,
                retire_after=retire_after,
            )
        )
    return tuple(keys)


def _allowed_github_host(host: str | None) -> bool:
    if not host:
        return False
    host = host.lower().rstrip(".")
    return host in ALLOWED_GITHUB_HOSTS or host.endswith(".githubusercontent.com")


def _validate_github_url(url: str) -> urllib.parse.SplitResult:
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or not _allowed_github_host(parsed.hostname)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in {None, 443}
    ):
        raise UpdateError(f"unsafe GitHub update URL: {url}")
    return parsed


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follow only HTTPS GitHub redirects and never leak credentials cross-origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        _validate_github_url(newurl)
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is None:
            return None
        old = urllib.parse.urlsplit(req.full_url)
        new = urllib.parse.urlsplit(newurl)
        if (old.scheme, old.hostname, old.port) != (new.scheme, new.hostname, new.port):
            redirected.headers.pop("Authorization", None)
            redirected.unredirected_hdrs.pop("Authorization", None)
        return redirected


def build_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(SafeRedirectHandler())


def github_request(
    opener: urllib.request.OpenerDirector,
    url: str,
    *,
    token: str | None,
    accept: str,
):
    _validate_github_url(url)
    headers = {
        "Accept": accept,
        "User-Agent": "HostPanel-Updater/2",
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


def _decode_release_document(payload: bytes) -> object:
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateError(f"GitHub returned an invalid release document: {exc}") from exc


def fetch_latest_release(
    opener: urllib.request.OpenerDirector, repository: str, token: str | None
) -> dict[str, object]:
    if REPOSITORY_RE.fullmatch(repository) is None:
        raise UpdateError("HP_UPDATE_REPOSITORY must be owner/repository")
    url = f"https://api.github.com/repos/{repository}/releases/latest"
    with github_request(opener, url, token=token, accept="application/vnd.github+json") as response:
        release = _decode_release_document(
            read_response(response, maximum=MAX_RELEASE_DOCUMENT_BYTES)
        )
    if not isinstance(release, dict):
        raise UpdateError("latest GitHub release document is not an object")
    return release


def fetch_release_candidates(
    opener: urllib.request.OpenerDirector,
    repository: str,
    token: str | None,
    channel: str,
) -> list[dict[str, object]]:
    if REPOSITORY_RE.fullmatch(repository) is None:
        raise UpdateError("HP_UPDATE_REPOSITORY must be owner/repository")
    if channel == "stable":
        return [fetch_latest_release(opener, repository, token)]
    url = (
        f"https://api.github.com/repos/{repository}/releases"
        f"?per_page={MAX_BETA_RELEASES}"
    )
    with github_request(opener, url, token=token, accept="application/vnd.github+json") as response:
        document = _decode_release_document(
            read_response(response, maximum=MAX_RELEASE_DOCUMENT_BYTES)
        )
    if not isinstance(document, list) or len(document) > MAX_BETA_RELEASES:
        raise UpdateError("GitHub beta release list has an unsafe shape")
    return [item for item in document if isinstance(item, dict)]


def _validate_release_metadata(release: dict[str, object]) -> tuple[bool, bool]:
    draft = release.get("draft")
    prerelease = release.get("prerelease")
    if not isinstance(draft, bool) or not isinstance(prerelease, bool):
        raise UpdateError("GitHub release flags must be booleans")
    if draft:
        raise UpdateError("GitHub release is still a draft")
    return draft, prerelease


def asset_map(release: dict[str, object]) -> dict[str, dict[str, object]]:
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise UpdateError("GitHub release does not contain an asset list")
    mapped: dict[str, dict[str, object]] = {}
    for item in assets:
        if not isinstance(item, dict):
            raise UpdateError("GitHub release asset metadata is invalid")
        name = item.get("name")
        url = item.get("url")
        size = item.get("size")
        if (
            not isinstance(name, str)
            or ASSET_RE.fullmatch(name) is None
            or not isinstance(url, str)
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size <= 0
        ):
            raise UpdateError("GitHub release asset metadata is invalid")
        if name in mapped:
            raise UpdateError(f"GitHub release contains duplicate asset: {name}")
        _validate_github_url(url)
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
    if (
        not isinstance(url, str)
        or not isinstance(advertised_size, int)
        or isinstance(advertised_size, bool)
    ):
        raise UpdateError("GitHub release asset metadata is invalid")
    if advertised_size <= 0 or advertised_size > maximum:
        raise UpdateError("GitHub release asset exceeds the configured size limit")
    with github_request(opener, url, token=token, accept="application/octet-stream") as response:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
        fd = os.open(destination, flags, 0o600)
        try:
            total = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > maximum or total > advertised_size:
                    raise UpdateError("GitHub release asset exceeds its advertised size")
                os.write(fd, chunk)
            if total != advertised_size:
                raise UpdateError(
                    f"GitHub release asset size mismatch: expected {advertised_size}, got {total}"
                )
            os.fsync(fd)
        finally:
            os.close(fd)


def _openssl_verify(key_payload: bytes, payload: pathlib.Path, signature: pathlib.Path) -> bool:
    with tempfile.TemporaryDirectory(prefix="hostpanel-key.") as temporary_name:
        key_path = pathlib.Path(temporary_name) / "captured.pub"
        key_path.write_bytes(key_payload)
        os.chmod(key_path, 0o600)
        completed = subprocess.run(
            [
                "openssl",
                "pkeyutl",
                "-verify",
                "-pubin",
                "-inkey",
                str(key_path),
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
    return completed.returncode == 0


def verify_signature(public_key: pathlib.Path, payload: pathlib.Path, signature: pathlib.Path) -> None:
    key_payload = read_regular_file(
        public_key,
        maximum=MAX_PUBLIC_KEY_BYTES,
        require_root_owner=(os.geteuid() == 0),
        expected_mode=0o644,
    )
    read_regular_file(signature, maximum=MAX_SIGNATURE_BYTES)
    if not _openssl_verify(key_payload, payload, signature):
        raise UpdateError("release signature verification failed")


def verify_with_keyring(
    keys: tuple[TrustedKey, ...],
    version: Version,
    payload: pathlib.Path,
    signature: pathlib.Path,
) -> TrustedKey:
    read_regular_file(signature, maximum=MAX_SIGNATURE_BYTES)
    eligible = [key for key in keys if key.accepts(version)]
    if not eligible:
        raise UpdateError("no trusted signing key is active for this release version")
    for key in eligible:
        if _openssl_verify(key.payload, payload, signature):
            return key
    raise UpdateError("release signature did not match an active trusted key")


def verify_with_key(
    key: TrustedKey, payload: pathlib.Path, signature: pathlib.Path
) -> None:
    read_regular_file(signature, maximum=MAX_SIGNATURE_BYTES)
    if not _openssl_verify(key.payload, payload, signature):
        raise UpdateError("archive signature does not match the manifest signing key")


def select_asset(assets: dict[str, dict[str, object]], name: str) -> dict[str, object]:
    item = assets.get(name)
    if item is None:
        raise UpdateError(f"GitHub release is missing asset: {name}")
    return item


def resolve_release(
    opener: urllib.request.OpenerDirector,
    repository: str,
    token: str | None,
    channel: str,
    keyring: tuple[TrustedKey, ...],
    temporary: pathlib.Path,
) -> ResolvedRelease:
    if channel not in {"stable", "beta"}:
        raise UpdateError("HP_UPDATE_CHANNEL must be stable or beta")
    candidates = fetch_release_candidates(opener, repository, token, channel)
    resolved: list[ResolvedRelease] = []
    failures: list[str] = []
    for index, release in enumerate(candidates):
        try:
            _, prerelease = _validate_release_metadata(release)
            if channel == "stable" and prerelease:
                raise UpdateError("stable channel release is marked prerelease")
            if channel == "beta" and not prerelease:
                continue
            assets = asset_map(release)
            candidate_dir = temporary / f"candidate-{index}"
            candidate_dir.mkdir(mode=0o700)
            manifest_name = "hostpanel-update-manifest.json"
            signature_name = f"{manifest_name}.sig"
            manifest_path = candidate_dir / manifest_name
            signature_path = candidate_dir / signature_name
            download_asset(
                opener,
                select_asset(assets, manifest_name),
                manifest_path,
                token=token,
                maximum=MAX_MANIFEST_BYTES,
            )
            download_asset(
                opener,
                select_asset(assets, signature_name),
                signature_path,
                token=token,
                maximum=MAX_SIGNATURE_BYTES,
            )
            manifest_payload = read_regular_file(
                manifest_path, maximum=MAX_MANIFEST_BYTES
            )
            manifest = ReleaseManifest.from_bytes(manifest_payload)
            if manifest.channel != channel:
                raise UpdateError("signed manifest channel does not match configured channel")
            version = Version.parse(manifest.version)
            signing_key = verify_with_keyring(
                keyring, version, manifest_path, signature_path
            )
            if release.get("tag_name") != manifest.tag:
                raise UpdateError("GitHub release tag does not match the signed manifest")
            if prerelease != version.is_prerelease:
                raise UpdateError("GitHub prerelease flag does not match signed version")
            release_url = release.get("url")
            if not isinstance(release_url, str):
                raise UpdateError("GitHub release URL is missing")
            _validate_github_url(release_url)
            resolved.append(
                ResolvedRelease(
                    release=release,
                    assets=assets,
                    manifest=manifest,
                    signing_key=signing_key,
                    manifest_url=release_url,
                )
            )
        except UpdateError as exc:
            failures.append(str(exc))
            if channel == "stable":
                raise
    if not resolved:
        detail = f": {'; '.join(failures[:3])}" if failures else ""
        raise UpdateError(f"no valid signed {channel} release was found{detail}")
    resolved.sort(key=lambda item: Version.parse(item.manifest.version))
    return resolved[-1]


def atomic_json(path: pathlib.Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    fd = os.open(temporary, flags, 0o600)
    try:
        encoded = (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode("utf-8")
        os.write(fd, encoded)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(temporary, path)


def record_status(path: pathlib.Path, *, state: str, message: str, **extra: object) -> None:
    payload: dict[str, object] = {
        "state": state,
        "message": message,
        "checked_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
    }
    payload.update(extra)
    atomic_json(path, payload)


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_extract(
    archive_path: pathlib.Path, destination: pathlib.Path, expected_root: str
) -> pathlib.Path:
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
                with source:
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        os.write(fd, chunk)
                os.fsync(fd)
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


def apply_release(source_root: pathlib.Path, manifest_url: str) -> None:
    environment = os.environ.copy()
    for name in (
        "PYTHONPATH",
        "PYTHONHOME",
        "BASH_ENV",
        "ENV",
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
    ):
        environment.pop(name, None)
    environment["PATH"] = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
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
    if channel not in {"stable", "beta"}:
        raise UpdateError("HP_UPDATE_CHANNEL must be stable or beta")
    token_file = pathlib.Path(config.get("HP_UPDATE_TOKEN_FILE", str(DEFAULT_TOKEN_FILE)))
    public_key = pathlib.Path(config.get("HP_UPDATE_PUBLIC_KEY", str(DEFAULT_PUBLIC_KEY)))
    keyring_file = pathlib.Path(config.get("HP_UPDATE_KEYRING", str(DEFAULT_KEYRING)))
    auto_update = config.get("HP_AUTO_UPDATE", "yes").lower() == "yes"
    require_token = config.get("HP_UPDATE_REQUIRE_TOKEN", "yes").lower() == "yes"
    if config.get("HP_AUTO_UPDATE", "yes").lower() not in {"yes", "no"}:
        raise UpdateError("HP_AUTO_UPDATE must be yes or no")
    if config.get("HP_UPDATE_REQUIRE_TOKEN", "yes").lower() not in {"yes", "no"}:
        raise UpdateError("HP_UPDATE_REQUIRE_TOKEN must be yes or no")
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
    keyring = load_keyring(keyring_file)
    configured_public_key = read_regular_file(
        public_key,
        maximum=MAX_PUBLIC_KEY_BYTES,
        require_root_owner=True,
        expected_mode=0o644,
        require_private_parent=True,
    )
    if not any(key.payload == configured_public_key for key in keyring):
        raise UpdateError("HP_UPDATE_PUBLIC_KEY is not present in the trusted keyring")
    opener = build_opener()
    with tempfile.TemporaryDirectory(prefix="hostpanel-update.") as temporary_name:
        temporary = pathlib.Path(temporary_name)
        resolved = resolve_release(
            opener, repository, token, channel, keyring, temporary
        )
        manifest = resolved.manifest
        if not Version.parse(installed) < Version.parse(manifest.version):
            record_status(
                status_file,
                state="current",
                message=f"HostPanel {installed} is current",
                installed_version=installed,
                available_version=manifest.version,
                commit=manifest.commit,
                signing_key=resolved.signing_key.key_id,
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
            signing_key=resolved.signing_key.key_id,
        )
        print(f"HostPanel {manifest.version} is available (installed: {installed}).")
        if args.check or (not args.apply and not args.dry_run) or (
            args.auto and not auto_update and not args.dry_run
        ):
            return 10
        archive_path = temporary / manifest.archive_name
        archive_sig_path = temporary / manifest.archive_signature_name
        download_asset(
            opener,
            select_asset(resolved.assets, manifest.archive_name),
            archive_path,
            token=token,
            maximum=MAX_ARCHIVE_BYTES,
        )
        download_asset(
            opener,
            select_asset(resolved.assets, manifest.archive_signature_name),
            archive_sig_path,
            token=token,
            maximum=MAX_SIGNATURE_BYTES,
        )
        if sha256_file(archive_path) != manifest.archive_sha256:
            raise UpdateError("release archive SHA-256 does not match the signed manifest")
        verify_with_key(resolved.signing_key, archive_path, archive_sig_path)
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
        apply_release(source_root, resolved.manifest_url)
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
            signing_key=resolved.signing_key.key_id,
        )
        print(f"HostPanel updated to {manifest.version}.")
        return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="check only; exit 10 when an update exists")
    mode.add_argument("--apply", action="store_true", help="apply a newer signed release")
    parser.add_argument(
        "--auto", action="store_true", help="honour HP_AUTO_UPDATE before applying"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="download and verify a newer release without installing; --apply is optional",
    )
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
