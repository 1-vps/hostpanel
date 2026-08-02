#!/usr/bin/env python3
"""Repair test expectations after the issue #115 integration transform."""

from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]


def replace_once(path: pathlib.Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"unexpected {label} shape: found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    ROOT / "tests" / "test_qemu_default_version.py",
    '''            'DEFAULT_EXPECTED_VERSION="$(tr -d '[:space:]' <"$REPO_ROOT/RELEASE_VERSION")"',''',
    '''            "DEFAULT_EXPECTED_VERSION=\\"$(tr -d '[:space:]' <\\"$REPO_ROOT/RELEASE_VERSION\\")\\"",''',
    "QEMU default-version assertion quoting",
)

old_qemu_method = '''    def test_expected_version_matches_signed_source_version(self):
        self.assertIn(
            f"HP_QEMU_EXPECTED_VERSION: {self.signed_version}",
            self.workflow,
        )
        self.assertIn(
            'EXPECTED_VERSION="${HP_QEMU_EXPECTED_VERSION:-',
            self.harness,
        )
        self.assertIn(
            f'EXPECTED_VERSION="${{HP_EXPECTED_VERSION:-{self.signed_version}}}"',
            self.validator,
        )
        self.assertNotIn("$REPO_ROOT/VERSION", self.harness)
        self.assertIn("HP_QEMU_EXPECTED_VERSION must be a release version", self.harness)
'''
new_qemu_method = '''    def test_expected_version_matches_reviewed_release_version(self):
        release_version = (ROOT / "RELEASE_VERSION").read_text(encoding="utf-8").strip()
        self.assertEqual(release_version, "3.4.1")
        self.assertNotIn("HP_QEMU_EXPECTED_VERSION:", self.workflow)
        self.assertIn(
            "DEFAULT_EXPECTED_VERSION=\\"$(tr -d '[:space:]' <\\"$REPO_ROOT/RELEASE_VERSION\\")\\"",
            self.harness,
        )
        self.assertIn(
            'EXPECTED_VERSION="${HP_QEMU_EXPECTED_VERSION:-$DEFAULT_EXPECTED_VERSION}"',
            self.harness,
        )
        self.assertIn(
            f'EXPECTED_VERSION="${{HP_EXPECTED_VERSION:-{release_version}}}"',
            self.validator,
        )
        self.assertNotIn("$REPO_ROOT/VERSION", self.harness)
        self.assertIn("HP_QEMU_EXPECTED_VERSION must be a release version", self.harness)
'''
replace_once(
    ROOT / "tests" / "test_qemu_vm_acceptance.py",
    old_qemu_method,
    new_qemu_method,
    "QEMU release-version contract",
)

replace_once(
    ROOT / "tests" / "test_updater_issue115.py",
    '        self.assertIn("/releases?per_page=", self.updater_source)',
    '        self.assertIn(\'f"?per_page={MAX_BETA_RELEASES}"\', self.updater_source)',
    "bounded beta release-list assertion",
)
