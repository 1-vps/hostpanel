#!/usr/bin/env python3
from __future__ import annotations

import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "localization-overlay.yml"


class LocalizationWorkflowSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        cls.pull_request_block = cls.text.split("  push:\n", 1)[0]

    def test_pull_requests_are_not_limited_to_main(self) -> None:
        self.assertIn("  pull_request:\n", self.pull_request_block)
        self.assertNotIn("branches: [main]", self.pull_request_block)
        self.assertIn("localization-overlay/**", self.pull_request_block)

    def test_signed_archive_is_verified_before_extraction(self) -> None:
        verify = self.text.index("      - name: Verify signed source archive")
        signature = self.text.index("openssl pkeyutl -verify", verify)
        extract = self.text.index("      - name: Safely extract signed source")
        self.assertLess(verify, signature)
        self.assertLess(signature, extract)
        self.assertIn("MCowBQYDK2VwAyEAJonL5vK2NRcFkXvKZUs64ISOs+FfhwL8gQVmFO4C0qk=", self.text)
        self.assertIn("expected one checksum", self.text)

    def test_extraction_rejects_unsafe_members_and_ambiguous_roots(self) -> None:
        for contract in (
            'path.is_absolute() or not path.parts or ".." in path.parts',
            "member.issym() or member.islnk() or member.isdev() or member.isfifo()",
            "max_members = 20000",
            "max_total_size = 200 * 1024 * 1024",
            "if len(roots) != 1",
            "source archive must contain one root",
            "extracted source root is unsafe or missing",
        ):
            self.assertIn(contract, self.text)
        self.assertNotIn("tar -xzf", self.text)
        self.assertNotIn("find localization-source", self.text)

    def test_extracted_root_is_reused_exactly(self) -> None:
        self.assertIn('/tmp/hostpanel-localization-root', self.text)
        self.assertGreaterEqual(
            self.text.count('SOURCE_ROOT="$(cat /tmp/hostpanel-localization-root)"'),
            2,
        )

    def test_checkout_is_read_only_and_bounded(self) -> None:
        self.assertIn("fetch-depth: 1", self.text)
        self.assertIn("persist-credentials: false", self.text)
        self.assertIn("permissions:\n  contents: read", self.text)
        self.assertIn("timeout-minutes: 25", self.text)
        self.assertIn("cancel-in-progress: true", self.text)


if __name__ == "__main__":
    unittest.main()
