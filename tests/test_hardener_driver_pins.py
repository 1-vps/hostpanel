from __future__ import annotations

import hashlib
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
ENTRY = ROOT / 'tools' / 'harden_install.py'
DRIVER = ROOT / 'tools' / 'harden_install_driver.py'
UPDATE_INSTALLER = ROOT / 'tools' / 'install-update-agent.sh'
BOOTSTRAP = ROOT / 'bootstrap-install.sh'


def git_blob_sha(path: pathlib.Path) -> str:
    payload = path.read_bytes()
    header = f'blob {len(payload)}\0'.encode('ascii')
    return hashlib.sha1(header + payload).hexdigest()


def constant(source: str, name: str) -> str:
    match = re.search(
        rf'^{re.escape(name)} = "([0-9a-f]{{40}})"$',
        source,
        re.MULTILINE,
    )
    if match is None:
        raise AssertionError(f'missing exact blob constant: {name}')
    return match.group(1)


class HardenerDriverPinTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.entry = ENTRY.read_text(encoding='utf-8')
        cls.bootstrap = BOOTSTRAP.read_text(encoding='utf-8')

    def test_driver_pin_matches_exact_git_blob(self):
        self.assertEqual(
            constant(self.entry, 'EXPECTED_DRIVER_BLOB'),
            git_blob_sha(DRIVER),
        )

    def test_update_installer_pin_matches_exact_git_blob(self):
        self.assertEqual(
            constant(self.entry, 'EXPECTED_UPDATE_INSTALLER_BLOB'),
            git_blob_sha(UPDATE_INSTALLER),
        )
        self.assertIn(
            'DRIVER.EXPECTED_UPDATE_AGENT_BLOBS["tools/install-update-agent.sh"]',
            self.entry,
        )

    def test_bootstrap_supplies_commit_verified_driver_root(self):
        self.assertIn(
            'HP_HARDENER_SOURCE_ROOT="$CHECKOUT"', self.bootstrap
        )
        self.assertIn(
            'source_root / "tools"', self.entry
        )
        self.assertIn(
            'return _trusted_driver_at(source_root / "tools")', self.entry
        )

    def test_public_entry_keeps_review_contract_visible(self):
        for marker in (
            'signed GitHub update agent installation',
            'Could not resolve exactly one reviewed GitHub update agent',
            'for backup in \\"${TREE_ROLLBACK_BACKUPS[@]}\\"; do',
        ):
            self.assertIn(marker.replace('\\"', '"'), self.entry)
        self.assertIn('EXPECTED_UPDATE_AGENT_BLOBS', self.entry)


if __name__ == '__main__':
    unittest.main()
