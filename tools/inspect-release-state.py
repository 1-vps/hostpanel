#!/usr/bin/env python3
"""Print exact non-secret source literals used by runtime compatibility patches."""
from __future__ import annotations

import pathlib
import tarfile


ROOT = pathlib.Path(__file__).resolve().parents[1]
ARCHIVES = sorted(ROOT.glob("hostpanel-*-source.tar.gz"))
if len(ARCHIVES) != 1:
    raise SystemExit(f"expected exactly one source tarball, found {len(ARCHIVES)}")

TOKENS = (
    "platform_log_cursors",
    "traffic_cursors",
    'cursor["offset"]',
    "cursor['offset']",
    "excluded.offset",
    "system_user",
)
found = 0

with tarfile.open(ARCHIVES[0], "r:gz") as archive:
    members = {member.name: member for member in archive.getmembers()}
    for member in members.values():
        if not member.isfile():
            continue
        if pathlib.PurePosixPath(member.name).suffix.lower() not in {
            ".py", ".sh", ".sql"
        }:
            continue
        handle = archive.extractfile(member)
        if handle is None:
            continue
        lines = handle.read().decode("utf-8", errors="strict").splitlines()
        hits = [
            (number, line)
            for number, line in enumerate(lines, 1)
            if any(token in line for token in TOKENS)
        ]
        if not hits:
            continue
        print(f"### {member.name}")
        for number, line in hits:
            print(f"{number}: {line!r}")
            found += 1
        if member.name.endswith("/app/store.py"):
            context_lines = set()
            for number, line in hits:
                if "system_user" not in line:
                    continue
                context_lines.update(
                    range(max(1, number - 14), min(len(lines), number + 14) + 1)
                )
            if context_lines:
                print(f"### {member.name} system_user context")
                for number in sorted(context_lines):
                    print(f"{number}: {lines[number - 1]!r}")

    manifests = [
        name for name in members
        if name.endswith("/app/privileged-files.txt")
    ]
    if len(manifests) != 1:
        raise SystemExit(
            f"expected exactly one privileged-files manifest, found {len(manifests)}"
        )
    manifest_name = manifests[0]
    manifest_handle = archive.extractfile(members[manifest_name])
    if manifest_handle is None:
        raise SystemExit("could not read privileged-files manifest")
    manifest_entries = [
        line.strip()
        for line in manifest_handle.read().decode("utf-8", errors="strict").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    release_prefix = manifest_name.split("/app/privileged-files.txt", 1)[0] + "/"
    app_prefix = release_prefix + "app/"
    print(f"### {manifest_name}")
    for entry in manifest_entries:
        fields = entry.split()
        path = fields[0]
        target = f"{release_prefix}{path}" if "/" in path else f"{app_prefix}{path}"
        print(
            f"{entry!r}: {'present' if target in members else 'MISSING'} "
            f"target={target!r}"
        )

if not found:
    raise SystemExit("no reviewed cursor source literals found")
