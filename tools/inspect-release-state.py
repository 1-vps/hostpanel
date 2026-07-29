#!/usr/bin/env python3
"""Print exact non-secret source literals used by runtime compatibility patches."""
from __future__ import annotations

import pathlib
import tarfile


ROOT = pathlib.Path(__file__).resolve().parents[1]
ARCHIVES = sorted(ROOT.glob("hostpanel-*-source.tar.gz"))
if len(ARCHIVES) != 1:
    raise SystemExit(f"expected exactly one source tarball, found {len(ARCHIVES)}")

TARGETS = {
    "/app/store.py": ("traffic_cursors", "offset"),
    "/app/platform_store.py": ("platform_log_cursors", 'cursor["offset"]', "offset"),
}
found: set[str] = set()

with tarfile.open(ARCHIVES[0], "r:gz") as archive:
    for member in archive.getmembers():
        target = next((suffix for suffix in TARGETS if member.name.endswith(suffix)), None)
        if target is None or not member.isfile():
            continue
        handle = archive.extractfile(member)
        if handle is None:
            continue
        lines = handle.read().decode("utf-8", errors="strict").splitlines()
        print(f"### {member.name}")
        for number, line in enumerate(lines, 1):
            if any(token in line for token in TARGETS[target]):
                print(f"{number}: {line!r}")
        found.add(target)

missing = set(TARGETS) - found
if missing:
    raise SystemExit(f"missing reviewed source members: {sorted(missing)}")
