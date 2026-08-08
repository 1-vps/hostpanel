from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import hostpanel_build_extras as extras
from hostpanel_build_config import BuildError


class MongoDbYamlGuardTests(unittest.TestCase):
    def test_duplicate_net_section_is_rejected(self) -> None:
        text = (
            'net:\n'
            '  bindIp: 127.0.0.1\n'
            'net:\n'
            '  bindIp: 0.0.0.0\n'
            'security:\n'
            '  authorization: enabled\n'
        )
        with self.assertRaisesRegex(
            BuildError, 'multiple top-level net sections'
        ):
            extras.harden_mongod_config(text)

    def test_duplicate_security_section_is_rejected(self) -> None:
        text = (
            'net:\n'
            '  bindIp: 127.0.0.1\n'
            'security:\n'
            '  authorization: enabled\n'
            'security:\n'
            '  authorization: disabled\n'
        )
        with self.assertRaisesRegex(
            BuildError, 'multiple top-level security sections'
        ):
            extras.harden_mongod_config(text)

    def test_commented_block_headers_are_normalized(self) -> None:
        text = (
            'net: # local-only listener\n'
            '  bindIp: 127.0.0.1\n'
            'security: # require authentication\n'
            '  authorization: enabled\n'
        )
        hardened = extras.harden_mongod_config(text)
        lines = hardened.splitlines()
        self.assertEqual(lines.count('net:'), 1)
        self.assertEqual(lines.count('security:'), 1)
        self.assertIn('  bindIp: 127.0.0.1', hardened)
        self.assertIn('  authorization: enabled', hardened)

    def test_inline_mapping_is_rejected_instead_of_duplicated(self) -> None:
        with self.assertRaisesRegex(
            BuildError, 'unsupported inline net mapping'
        ):
            extras.harden_mongod_config(
                'net: {bindIp: 0.0.0.0}\nsecurity:\n'
                '  authorization: enabled\n'
            )


if __name__ == '__main__':
    unittest.main()
