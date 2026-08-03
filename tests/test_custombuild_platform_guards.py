from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
sys.path.insert(0, str(TOOLS))

import hostpanel_build_config as CONFIG
import hostpanel_build_mongodb_adapter as MONGODB


class CustomBuildPlatformGuardTests(unittest.TestCase):
    def test_mongodb_accepts_only_supported_hostpanel_overlap(self):
        accepted = (
            (CONFIG.Platform('debian', 'ubuntu', '22.04'), ('ubuntu', 'jammy')),
            (CONFIG.Platform('debian', 'ubuntu', '24.04'), ('ubuntu', 'noble')),
            (CONFIG.Platform('debian', 'debian', '12'), ('debian', 'bookworm')),
            (CONFIG.Platform('rhel', 'rocky', '9.4'), ('rhel', '9')),
            (CONFIG.Platform('rhel', 'almalinux', '9.6'), ('rhel', '9')),
        )
        for platform, expected in accepted:
            with self.subTest(platform=platform):
                self.assertEqual(MONGODB.mongodb_supported(platform, 'x86_64'), expected)

    def test_mongodb_rejects_non_overlap_or_unpublished_targets(self):
        rejected = (
            CONFIG.Platform('debian', 'ubuntu', '20.04'),
            CONFIG.Platform('debian', 'ubuntu', '26.04'),
            CONFIG.Platform('debian', 'debian', '13'),
            CONFIG.Platform('rhel', 'rocky', '10.0'),
            CONFIG.Platform('rhel', 'almalinux', '10.0'),
            CONFIG.Platform('rhel', 'rhel', '9.4'),
            CONFIG.Platform('rhel', 'centos', '9'),
        )
        for platform in rejected:
            with self.subTest(platform=platform), self.assertRaises(CONFIG.BuildError):
                MONGODB.mongodb_supported(platform, 'x86_64')

    def test_mongodb_rejects_arm64_even_on_supported_os(self):
        with self.assertRaises(CONFIG.BuildError):
            MONGODB.mongodb_supported(
                CONFIG.Platform('debian', 'ubuntu', '24.04'), 'aarch64'
            )


if __name__ == '__main__':
    unittest.main()
