#!/usr/bin/env python3
"""Compare source archives without printing member contents."""

from __future__ import annotations

import argparse
import hashlib
import pathlib
import tarfile
from dataclasses import dataclass


@dataclass(frozen=True)
class MemberRecord:
    kind: str
    size: int
    mode: int
    uid: int
    gid: int
    mtime: int
    sha256: str | None


def digest_member(archive: tarfile.TarFile, member: tarfile.TarInfo) -> str:
    handle = archive.extractfile(member)
    if handle is None:
        raise RuntimeError(f"could not read regular member: {member.name}")
    digest = hashlib.sha256()
    with handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inventory(path: pathlib.Path) -> dict[str, MemberRecord]:
    records: dict[str, MemberRecord] = {}
    with tarfile.open(path, "r:gz") as archive:
        for member in archive.getmembers():
            name = member.name.rstrip("/")
            if name in records:
                raise RuntimeError(f"duplicate archive member: {name}")
            if member.isfile():
                kind = "file"
                digest = digest_member(archive, member)
            elif member.isdir():
                kind = "directory"
                digest = None
            else:
                kind = "unsupported"
                digest = None
            records[name] = MemberRecord(
                kind=kind,
                size=member.size,
                mode=member.mode,
                uid=member.uid,
                gid=member.gid,
                mtime=int(member.mtime),
                sha256=digest,
            )
    return records


def compare(left: pathlib.Path, right: pathlib.Path) -> list[str]:
    left_records = inventory(left)
    right_records = inventory(right)
    differences: list[str] = []
    for name in sorted(set(left_records) | set(right_records)):
        left_record = left_records.get(name)
        right_record = right_records.get(name)
        if left_record is None:
            differences.append(f"only in right: {name}")
        elif right_record is None:
            differences.append(f"only in left: {name}")
        elif left_record != right_record:
            fields = [
                field
                for field in MemberRecord.__dataclass_fields__
                if getattr(left_record, field) != getattr(right_record, field)
            ]
            differences.append(f"member differs ({','.join(fields)}): {name}")
    return differences


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("left", type=pathlib.Path)
    parser.add_argument("right", type=pathlib.Path)
    args = parser.parse_args()
    differences = compare(args.left, args.right)
    if differences:
        print(f"source archives differ in {len(differences)} member(s)")
        for difference in differences[:50]:
            print(difference)
        if len(differences) > 50:
            print(f"... {len(differences) - 50} additional difference(s) omitted")
        return 1
    print("source archive member inventories are identical")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
