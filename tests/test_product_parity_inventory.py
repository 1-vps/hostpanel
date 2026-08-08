from __future__ import annotations

import hashlib
import io
import json
import pathlib
import stat
import sys
import tarfile
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import audit_product_parity as parity


class ProductParityInventoryTests(unittest.TestCase):
    def make_archive(
        self,
        root: pathlib.Path,
        members: list[tuple[str, bytes, str]],
    ) -> tuple[pathlib.Path, pathlib.Path]:
        archive = root / "hostpanel-test-source.tar.gz"
        with tarfile.open(archive, "w:gz") as handle:
            for name, payload, kind in members:
                info = tarfile.TarInfo(name)
                if kind == "file":
                    info.size = len(payload)
                    info.mode = 0o755 if name.endswith("hostpanel-worker") else 0o644
                    handle.addfile(info, io.BytesIO(payload))
                elif kind == "dir":
                    info.type = tarfile.DIRTYPE
                    info.mode = 0o755
                    handle.addfile(info)
                elif kind == "symlink":
                    info.type = tarfile.SYMTYPE
                    info.linkname = "target"
                    handle.addfile(info)
                else:
                    raise AssertionError(kind)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        checksums = root / "SHA256SUMS"
        checksums.write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
        archive.chmod(0o644)
        checksums.chmod(0o644)
        return archive, checksums

    def test_builds_bounded_inventory_without_extracting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            archive, checksums = self.make_archive(
                root,
                [
                    ("hostpanel/", b"", "dir"),
                    (
                        "hostpanel/app/main.py",
                        b"""
@app.get('/api/v1/sites')
def sites():
    return {'openapi': True, 'webhook': 'ready'}
""",
                        "file",
                    ),
                    (
                        "hostpanel/app/templates/panel.html",
                        b'<template data-page-template="deployments"></template>',
                        "file",
                    ),
                    (
                        "hostpanel/bin/hostpanel-worker",
                        b"#!/bin/sh\nexec true\n",
                        "file",
                    ),
                ],
            )

            result = parity.audit_archive(archive, checksums)

            self.assertEqual(result["schema"], 1)
            self.assertEqual(result["archive"]["top_level_root"], "hostpanel")
            inventory = result["inventory"]
            self.assertIn("/api/v1/sites", inventory["routes"])
            self.assertIn("/api/v1/sites", inventory["api_paths"])
            self.assertEqual(inventory["panel_pages"], ["deployments"])
            self.assertIn(
                "hostpanel/bin/hostpanel-worker",
                inventory["command_candidates"],
            )
            api_markers = inventory["capability_markers"]["api_automation"]
            self.assertEqual(api_markers["matches"], 2)
            self.assertEqual(
                api_markers["evidence_files"], ["hostpanel/app/main.py"]
            )

    def test_rejects_traversal_and_link_members(self) -> None:
        cases = (
            ([("hostpanel/../escape", b"x", "file")], "unsafe source archive member"),
            (
                [
                    ("hostpanel/", b"", "dir"),
                    ("hostpanel/link", b"", "symlink"),
                ],
                "linked source archive member is forbidden",
            ),
        )
        for members, message in cases:
            with self.subTest(message=message), tempfile.TemporaryDirectory() as directory:
                root = pathlib.Path(directory)
                archive, checksums = self.make_archive(root, members)
                with self.assertRaisesRegex(parity.AuditError, message):
                    parity.audit_archive(archive, checksums)

    def test_rejects_checksum_mismatch_before_tar_scan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            archive, checksums = self.make_archive(
                root,
                [("hostpanel/readme.txt", b"hello\n", "file")],
            )
            checksums.write_text(
                f"{'0' * 64}  {archive.name}\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(parity.AuditError, "checksum mismatch"):
                parity.audit_archive(archive, checksums)

    def test_rejects_multiple_top_level_roots_and_duplicate_members(self) -> None:
        cases = (
            (
                [
                    ("hostpanel/a.txt", b"a", "file"),
                    ("other/b.txt", b"b", "file"),
                ],
                "exactly one top-level root",
            ),
            (
                [
                    ("hostpanel/a.txt", b"a", "file"),
                    ("hostpanel/a.txt", b"b", "file"),
                ],
                "duplicate source archive member",
            ),
        )
        for members, message in cases:
            with self.subTest(message=message), tempfile.TemporaryDirectory() as directory:
                root = pathlib.Path(directory)
                archive, checksums = self.make_archive(root, members)
                with self.assertRaisesRegex(parity.AuditError, message):
                    parity.audit_archive(archive, checksums)

    def test_cli_output_is_deterministic_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            archive, checksums = self.make_archive(
                root,
                [("hostpanel/readme.md", b"WordPress staging webhook\n", "file")],
            )
            output = root / "inventory.json"
            status = parity.main(
                [
                    "--root", str(root),
                    "--archive", str(archive),
                    "--sha256sums", str(checksums),
                    "--output", str(output),
                ]
            )
            self.assertEqual(status, 0)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema"], 1)
            self.assertTrue(output.read_text(encoding="utf-8").endswith("\n"))

    def test_repository_signed_archive_is_inventory_compatible(self) -> None:
        archives = sorted(ROOT.glob("hostpanel-*-source.tar.gz"))
        if not archives:
            self.skipTest("repository signed source archive is not present")
        self.assertEqual(len(archives), 1)
        result = parity.audit_archive(archives[0], ROOT / "SHA256SUMS")
        self.assertGreater(result["safety"]["member_count"], 0)
        self.assertGreater(result["safety"]["text_files_scanned"], 0)
        self.assertEqual(result["archive"]["name"], archives[0].name)

    def test_output_symlink_is_rejected_without_touching_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            victim = root / "victim.json"
            victim.write_text("victim\n", encoding="utf-8")
            output = root / "inventory.json"
            output.symlink_to(victim)
            with self.assertRaisesRegex(parity.AuditError, "unsafe inventory output target"):
                parity.write_json({"schema": 1}, output)
            self.assertEqual(victim.read_text(encoding="utf-8"), "victim\n")
            self.assertTrue(output.is_symlink())

    def test_rejects_symlink_archive_without_touching_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            archive, checksums = self.make_archive(
                root,
                [("hostpanel/readme.txt", b"hello\n", "file")],
            )
            link = root / "hostpanel-link-source.tar.gz"
            link.symlink_to(archive)
            with self.assertRaisesRegex(parity.AuditError, "unsafe signed source archive"):
                parity.audit_archive(link, checksums)
            self.assertTrue(link.is_symlink())
            self.assertTrue(stat.S_ISREG(archive.stat().st_mode))


if __name__ == "__main__":
    unittest.main()
