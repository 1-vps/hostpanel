#!/usr/bin/env python3
"""Create a bounded capability inventory from HostPanel's signed source archive."""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import pathlib
import re
import secrets
import stat
import sys
import tarfile

MAX_ARCHIVE = 512 << 20
MAX_EXPANDED = 200 << 20
MAX_TEXT = 4 << 20
MAX_TEXT_TOTAL = 64 << 20
MAX_MEMBERS = 20_000
MAX_RESULTS = 10_000
TEXT_SUFFIXES = {
    ".cfg", ".conf", ".css", ".html", ".ini", ".js", ".json", ".md",
    ".py", ".sh", ".sql", ".toml", ".txt", ".xml", ".yaml", ".yml",
}
TERMS = {
    "api_automation": ("openapi", "swagger", "api token", "service account", "webhook"),
    "git_and_staging": ("git deploy", "git repository", "staging", "preview environment"),
    "migration": ("migration", "cpanel", "plesk", "directadmin", "rsync"),
    "remote_backup": ("s3-compatible", "backblaze", "wasabi", "azure blob", "rclone"),
    "reseller_and_plans": ("reseller", "service plan", "white-label", "oversell"),
    "application_runtimes": ("node.js", "wsgi", "asgi", "container runtime", "reverse proxy"),
    "wordpress_operations": ("wordpress", "wp-cli", "plugin update", "theme update", "malware scan"),
    "observability": ("prometheus", "opentelemetry", "alert rule", "uptime check", "structured log"),
    "multi_server": ("multi-server", "dns cluster", "node inventory", "drain mode"),
    "extensions_and_identity": ("extension sdk", "plugin sdk", "webauthn", "passkey", "oidc", "saml"),
}
ROUTE_RE = re.compile(
    r"(?:@(?:[A-Za-z_]\w*\.)?(?:route|get|post|put|patch|delete)|"
    r"(?:add_url_rule|add_api_route))\(\s*[rubfRUBF]*['\"](?P<path>/[^'\"]*)"
)
API_RE = re.compile(r"/api/[A-Za-z0-9_./{}:~-]{1,240}")
PAGE_RE = re.compile(
    r"(?:data-page-template|data-page)\s*=\s*['\"](?P<page>[A-Za-z0-9_.:-]{1,128})['\"]"
)


class AuditError(RuntimeError):
    pass


def same_file(a: os.stat_result, b: os.stat_result) -> bool:
    fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink",
              "st_size", "st_mtime_ns", "st_ctime_ns")
    return all(getattr(a, name) == getattr(b, name) for name in fields)


def inspect_regular(path: pathlib.Path, maximum: int, label: str) -> os.stat_result:
    try:
        meta = path.lstat()
    except OSError as exc:
        raise AuditError(f"cannot inspect {label}: {path}") from exc
    if (not stat.S_ISREG(meta.st_mode) or stat.S_ISLNK(meta.st_mode)
            or meta.st_nlink != 1 or stat.S_IMODE(meta.st_mode) & 0o022
            or not 0 < meta.st_size <= maximum):
        raise AuditError(f"unsafe {label}: {path}")
    return meta


def open_verified(path: pathlib.Path, before: os.stat_result, label: str) -> int:
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
        opened = os.fstat(fd)
    except OSError as exc:
        raise AuditError(f"cannot open {label}: {path}") from exc
    if not same_file(before, opened):
        os.close(fd)
        raise AuditError(f"{label} changed before open: {path}")
    return fd


def read_text(path: pathlib.Path, maximum: int, label: str) -> str:
    before = inspect_regular(path, maximum, label)
    fd = open_verified(path, before, label)
    try:
        chunks, remaining = [], before.st_size
        while remaining:
            chunk = os.read(fd, min(remaining, 65536))
            if not chunk:
                raise AuditError(f"{label} ended unexpectedly: {path}")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(fd, 1):
            raise AuditError(f"{label} grew during read: {path}")
        after, current = os.fstat(fd), path.lstat()
        if not same_file(before, after) or not same_file(after, current):
            raise AuditError(f"{label} changed during read: {path}")
    finally:
        os.close(fd)
    try:
        return b"".join(chunks).decode("utf-8", "strict")
    except UnicodeError as exc:
        raise AuditError(f"{label} is not UTF-8: {path}") from exc


def expected_hash(manifest: pathlib.Path, name: str) -> str:
    matches = []
    for line in read_text(manifest, 1 << 20, "SHA256SUMS").splitlines():
        if not line.strip():
            continue
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            raise AuditError("SHA256SUMS contains a malformed line")
        digest, filename = parts[0], parts[1].lstrip("*")
        if filename == name:
            if not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise AuditError("SHA256SUMS contains an invalid digest")
            matches.append(digest)
    if len(matches) != 1:
        raise AuditError(f"expected one checksum for {name}, found {len(matches)}")
    return matches[0]


def safe_member(name: str) -> pathlib.PurePosixPath:
    path = pathlib.PurePosixPath(name)
    if (not name or path.is_absolute() or not path.parts
            or any(part in {"", ".", ".."} for part in path.parts)):
        raise AuditError(f"unsafe source archive member: {name!r}")
    return path


def add_bounded(target: set[str], value: str, label: str) -> None:
    target.add(value)
    if len(target) > MAX_RESULTS:
        raise AuditError(f"too many {label} discoveries")


def scan_text(text: str, name: str, inventory: dict[str, object]) -> None:
    for match in ROUTE_RE.finditer(text):
        add_bounded(inventory["routes"], match.group("path"), "route")
    for match in API_RE.finditer(text):
        add_bounded(inventory["api_paths"], match.group(0), "API path")
    for match in PAGE_RE.finditer(text):
        add_bounded(inventory["panel_pages"], match.group("page"), "panel page")
    folded = text.casefold().replace("_", " ").replace("-", " ")
    for category, terms in TERMS.items():
        count = sum(folded.count(term.replace("-", " ")) for term in terms)
        if count:
            inventory["markers"][category]["matches"] += count
            files = inventory["markers"][category]["evidence_files"]
            if len(files) < 100:
                files.add(name)


def audit_archive(archive: pathlib.Path, manifest: pathlib.Path) -> dict[str, object]:
    before = inspect_regular(archive, MAX_ARCHIVE, "signed source archive")
    expected = expected_hash(manifest, archive.name)
    fd = open_verified(archive, before, "signed source archive")
    inv = {
        "routes": set(), "api_paths": set(), "panel_pages": set(),
        "command_candidates": set(),
        "markers": {name: {"matches": 0, "evidence_files": set()} for name in TERMS},
    }
    names, roots = set(), set()
    suffixes: collections.Counter[str] = collections.Counter()
    members = expanded = scanned = text_files = oversized = non_utf8 = 0
    try:
        with os.fdopen(fd, "rb", closefd=False) as stream:
            digest = hashlib.sha256()
            for chunk in iter(lambda: stream.read(1 << 20), b""):
                digest.update(chunk)
            actual = digest.hexdigest()
            if actual != expected:
                raise AuditError(f"signed source archive checksum mismatch: expected {expected}, got {actual}")
            stream.seek(0)
            try:
                opened = tarfile.open(fileobj=stream, mode="r|gz")
            except (tarfile.TarError, OSError) as exc:
                raise AuditError("cannot open signed source archive") from exc
            with opened:
                for member in opened:
                    members += 1
                    if members > MAX_MEMBERS:
                        raise AuditError("source archive member count exceeds safety limit")
                    path = safe_member(member.name)
                    if member.name in names:
                        raise AuditError(f"duplicate source archive member: {member.name}")
                    names.add(member.name)
                    roots.add(path.parts[0])
                    if len(roots) > 1:
                        raise AuditError("source archive must contain exactly one top-level root")
                    if member.issym() or member.islnk():
                        raise AuditError(f"linked source archive member is forbidden: {member.name}")
                    if not (member.isdir() or member.isfile()):
                        raise AuditError(f"unsupported source archive member: {member.name}")
                    expanded += member.size
                    if member.size < 0 or expanded > MAX_EXPANDED:
                        raise AuditError("source archive expands beyond the safety limit")
                    if member.isdir():
                        continue
                    suffix = path.suffix.lower()
                    suffixes[suffix or "<none>"] += 1
                    if member.mode & 0o111 or path.name.startswith(("hostpanel-", "hostpanel_")):
                        add_bounded(inv["command_candidates"], member.name, "command candidate")
                    if suffix not in TEXT_SUFFIXES:
                        continue
                    if member.size > MAX_TEXT:
                        oversized += 1
                        continue
                    if scanned + member.size > MAX_TEXT_TOTAL:
                        raise AuditError("text scan exceeds the safety limit")
                    handle = opened.extractfile(member)
                    if handle is None:
                        raise AuditError(f"cannot read source archive member: {member.name}")
                    payload = handle.read(member.size + 1)
                    if len(payload) != member.size:
                        raise AuditError(f"source archive member size changed: {member.name}")
                    scanned += member.size
                    try:
                        text = payload.decode("utf-8", "strict")
                    except UnicodeError:
                        non_utf8 += 1
                        continue
                    text_files += 1
                    scan_text(text, member.name, inv)
            after, current = os.fstat(fd), archive.lstat()
            if not same_file(before, after) or not same_file(after, current):
                raise AuditError("signed source archive changed during inspection")
    finally:
        os.close(fd)
    if len(roots) != 1:
        raise AuditError("source archive must contain exactly one top-level root")
    markers = {
        name: {"matches": data["matches"], "evidence_files": sorted(data["evidence_files"])}
        for name, data in sorted(inv["markers"].items())
    }
    return {
        "schema": 1,
        "archive": {"name": archive.name, "sha256": expected, "top_level_root": next(iter(roots))},
        "safety": {
            "member_count": members, "uncompressed_bytes": expanded,
            "text_files_scanned": text_files, "text_bytes_scanned": scanned,
            "oversized_text_files_skipped": oversized,
            "non_utf8_text_files_skipped": non_utf8,
        },
        "inventory": {
            "routes": sorted(inv["routes"]), "api_paths": sorted(inv["api_paths"]),
            "panel_pages": sorted(inv["panel_pages"]),
            "command_candidates": sorted(inv["command_candidates"]),
            "file_suffix_counts": dict(sorted(suffixes.items())),
            "capability_markers": markers,
        },
    }


def fsync_dir(path: pathlib.Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_DIRECTORY", 0)
                 | getattr(os, "O_NOFOLLOW", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_json(payload: dict[str, object], destination: pathlib.Path | None) -> None:
    encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
    if destination is None:
        sys.stdout.buffer.write(encoded)
        return
    parent, uid = destination.parent, os.geteuid()
    meta = parent.lstat()
    if (not stat.S_ISDIR(meta.st_mode) or stat.S_ISLNK(meta.st_mode)
            or meta.st_uid != uid or stat.S_IMODE(meta.st_mode) & 0o022):
        raise AuditError(f"unsafe inventory output directory: {parent}")
    if os.path.lexists(destination):
        old = destination.lstat()
        if (not stat.S_ISREG(old.st_mode) or stat.S_ISLNK(old.st_mode)
                or old.st_uid != uid or old.st_nlink != 1
                or stat.S_IMODE(old.st_mode) & 0o022):
            raise AuditError(f"unsafe inventory output target: {destination}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    for _ in range(32):
        temporary = destination.with_name(f".{destination.name}.parity.{secrets.token_hex(12)}")
        try:
            fd = os.open(temporary, flags, 0o600)
            break
        except FileExistsError:
            continue
    else:
        raise AuditError("could not allocate inventory output temporary")
    active = cleanup = None
    try:
        view = memoryview(encoded)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise AuditError("could not complete inventory output write")
            view = view[written:]
        os.fsync(fd)
        os.fchmod(fd, 0o600)
        descriptor, fd = fd, -1
        os.close(descriptor)
        os.replace(temporary, destination)
        fsync_dir(parent)
    except BaseException as exc:
        active = exc
        raise
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except BaseException as exc:
                cleanup = exc
        try:
            temporary.unlink(missing_ok=True)
        except BaseException as exc:
            cleanup = cleanup or exc
        if active is None and cleanup is not None:
            raise cleanup


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=pathlib.Path, default=pathlib.Path.cwd())
    parser.add_argument("--archive", type=pathlib.Path)
    parser.add_argument("--sha256sums", type=pathlib.Path)
    parser.add_argument("--output", type=pathlib.Path)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    archives = sorted(root.glob("hostpanel-*-source.tar.gz"))
    archive = args.archive.resolve() if args.archive else (
        archives[0] if len(archives) == 1 else None
    )
    if archive is None:
        print(f"HostPanel parity inventory failed: expected one signed source archive, found {len(archives)}", file=sys.stderr)
        return 1
    manifest = args.sha256sums.resolve() if args.sha256sums else root / "SHA256SUMS"
    try:
        write_json(audit_archive(archive, manifest), args.output)
    except (AuditError, OSError, tarfile.TarError) as exc:
        print(f"HostPanel parity inventory failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
