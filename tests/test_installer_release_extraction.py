#!/usr/bin/env python3
from __future__ import annotations

import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "installer-hardening.yml"


class InstallerReleaseExtractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_runtime_lock_requires_one_regular_signed_archive(self) -> None:
        self.assertIn('test ! -L SHA256SUMS', self.text)
        self.assertIn('mapfile -t archives', self.text)
        self.assertIn('test "${#archives[@]}" -eq 1', self.text)
        self.assertIn('test ! -L "$archive"', self.text)
        self.assertIn('test ! -L "$archive.sig"', self.text)
        self.assertIn('openssl pkeyutl -verify -pubin', self.text)

    def test_runtime_lock_extracts_source_fail_closed(self) -> None:
        self.assertNotIn('tar -xzf "$archive"', self.text)
        for contract in (
            'path.is_absolute() or ".." in path.parts',
            'member.issym() or member.islnk()',
            'not (member.isdir() or member.isfile())',
            'len(members) > 20000',
            'total > 200 * 1024 * 1024',
            'len(roots) != 1',
            'root.is_symlink()',
        ):
            with self.subTest(contract=contract):
                self.assertIn(contract, self.text)
        self.assertIn('release_root=$(cat /tmp/hostpanel-release-root)', self.text)

    def test_release_is_verified_before_extraction(self) -> None:
        verify = self.text.index('openssl pkeyutl -verify -pubin')
        extract = self.text.index('with tarfile.open(archive, "r:gz") as handle:')
        requirements = self.text.index('test -s "$release_root/requirements.lock"')
        self.assertLess(verify, extract)
        self.assertLess(extract, requirements)


if __name__ == "__main__":
    unittest.main()
