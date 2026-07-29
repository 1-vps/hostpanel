#!/usr/bin/env python3
"""Print non-secret source snippets around PostgreSQL reserved identifiers."""
from __future__ import annotations

import pathlib
import re
import tarfile


ROOT = pathlib.Path(__file__).resolve().parents[1]
ARCHIVES = sorted(ROOT.glob("hostpanel-*-source.tar.gz"))
if len(ARCHIVES) != 1:
    raise SystemExit(f"expected exactly one source tarball, found {len(ARCHIVES)}")

PATTERNS = (
    re.compile(r"\boffset\b", re.I),
    re.compile(r"\blimit\b", re.I),
)
interesting_names: set[str] = set()

with tarfile.open(ARCHIVES[0], "r:gz") as archive:
    for member in archive.getmembers():
        if not member.isfile():
            continue
        if pathlib.PurePosixPath(member.name).suffix.lower() not in {
            ".py", ".sql", ".sh", ".html", ".js", ".json", ".toml", ".yaml", ".yml"
        }:
            continue
        handle = archive.extractfile(member)
        if handle is None:
            continue
        text = handle.read().decode("utf-8", errors="replace")
        lines = text.splitlines()
        hits = [
            index
            for index, line in enumerate(lines)
            if any(pattern.search(line) for pattern in PATTERNS)
        ]
        if not hits:
            continue
        interesting_names.add(member.name)
        print(f"\n### {member.name}")
        emitted: set[int] = set()
        for hit in hits:
            start = max(0, hit - 10)
            end = min(len(lines), hit + 11)
            for index in range(start, end):
                if index in emitted:
                    continue
                emitted.add(index)
                print(f"{index + 1:5d}: {lines[index]}")

if not interesting_names:
    raise SystemExit("no offset/limit source snippets found")
print("\nRelevant files:")
for name in sorted(interesting_names):
    print(name)
