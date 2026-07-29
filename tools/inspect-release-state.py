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
)
found = 0

with tarfile.open(ARCHIVES[0], "r:gz") as archive:
    for member in archive.getmembers():
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

if not found:
    raise SystemExit("no reviewed cursor source literals found")
