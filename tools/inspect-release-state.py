#!/usr/bin/env python3
"""Print focused, non-secret state/bootstrap snippets from the signed source archive."""
from __future__ import annotations

import pathlib
import tarfile


ROOT = pathlib.Path(__file__).resolve().parents[1]
ARCHIVES = sorted(ROOT.glob("hostpanel-*-source.tar.gz"))
if len(ARCHIVES) != 1:
    raise SystemExit(f"expected exactly one source tarball, found {len(ARCHIVES)}")

needles = (
    "server_groups",
    "def init",
    "def connect",
    "def executescript",
    "executescript(",
    "HP_DATABASE_URL",
    "DATABASE_URL",
    "DATABASE_URL_FILE",
    "HP_DB",
    "dependencies =",
    "CREATE TABLE",
    "REFERENCES ",
)
interesting_names: set[str] = set()

with tarfile.open(ARCHIVES[0], "r:gz") as archive:
    members = [m for m in archive.getmembers() if m.isfile()]
    for member in members:
        lowered = member.name.lower()
        if not any(token in lowered for token in ("store.py", "migrat", "schema", "database", "config", "dbcompat")):
            continue
        handle = archive.extractfile(member)
        if handle is None:
            continue
        text = handle.read().decode("utf-8", errors="replace")
        lines = text.splitlines()
        hits = [i for i, line in enumerate(lines) if any(needle in line for needle in needles)]
        if not hits:
            continue
        interesting_names.add(member.name)
        print(f"\n### {member.name}")
        emitted: set[int] = set()
        for hit in hits:
            start = max(0, hit - 14)
            end = min(len(lines), hit + 24)
            for index in range(start, end):
                if index in emitted:
                    continue
                emitted.add(index)
                print(f"{index + 1:5d}: {lines[index]}")

if not interesting_names:
    raise SystemExit("no relevant state/bootstrap source snippets found")
print("\nRelevant files:")
for name in sorted(interesting_names):
    print(name)
