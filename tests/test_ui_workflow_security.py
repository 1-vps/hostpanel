from __future__ import annotations

import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ui-audit.yml"


class UiWorkflowSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = WORKFLOW.read_text(encoding="utf-8")

    def test_signed_archive_is_verified_before_any_archive_consumer(self) -> None:
        verify = self.source.index("      - name: Verify signed source archive")
        contracts = self.source.index("      - name: Validate repository UI overlay contracts")
        extract = self.source.index(
            "      - name: Build reviewed UI and localization source tree"
        )
        self.assertLess(verify, contracts)
        self.assertLess(verify, extract)
        self.assertIn("openssl pkeyutl -verify", self.source)
        self.assertIn(
            "MCowBQYDK2VwAyEAJonL5vK2NRcFkXvKZUs64ISOs+FfhwL8gQVmFO4C0qk=",
            self.source,
        )
        self.assertIn("expected one checksum", self.source)
        self.assertIn("test ! -L SHA256SUMS", self.source)
        self.assertIn('test ! -L "$archive"', self.source)
        self.assertIn('test ! -L "$archive.sig"', self.source)
        self.assertEqual(self.source.count("      - SHA256SUMS\n"), 2)

    def test_checkout_and_python_bootstrap_are_bounded(self) -> None:
        self.assertIn("fetch-depth: 1", self.source)
        self.assertIn("persist-credentials: false", self.source)
        self.assertIn("permissions:\n  contents: read", self.source)
        self.assertIn("timeout-minutes: 45", self.source)
        self.assertIn("cancel-in-progress: true", self.source)
        self.assertIn("'pip==25.1.1'", self.source)
        self.assertNotIn("pip install --disable-pip-version-check --upgrade pip", self.source)

    def test_browser_install_uses_the_signed_local_dependency(self) -> None:
        self.assertIn(
            "./node_modules/.bin/playwright install --with-deps chromium",
            self.source,
        )
        self.assertNotIn("npx playwright install", self.source)
        self.assertLess(
            self.source.index("npm ci --ignore-scripts"),
            self.source.index("./node_modules/.bin/playwright install"),
        )

    def test_integrated_localization_overlay_is_applied_before_browser_tests(self) -> None:
        build = self.source.index(
            "      - name: Build reviewed UI and localization source tree"
        )
        apply_overlay = self.source.index(
            "python3 localization-overlay/apply_localization_overlay_reviewed.py",
            build,
        )
        install_browser_spec = self.source.index(
            "install -m 0644 tests/browser/panel-redesign.spec.js",
            apply_overlay,
        )
        chromium = self.source.index(
            "      - name: Run Chromium interaction and Axe audit",
            install_browser_spec,
        )
        self.assertLess(build, apply_overlay)
        self.assertLess(apply_overlay, install_browser_spec)
        self.assertLess(install_browser_spec, chromium)
        self.assertEqual(self.source.count("      - localization-overlay/**\n"), 2)
        self.assertGreaterEqual(
            self.source.count("test_localization_bootstrap_wiring.py"),
            3,
        )

    def test_security_regressions_are_executed_by_the_ui_workflow(self) -> None:
        for name in (
            "test_playwright_browser_compat.py",
            "test_ui_workflow_security.py",
            "test_localization_bootstrap_wiring.py",
        ):
            with self.subTest(name=name):
                self.assertGreaterEqual(self.source.count(name), 3)


if __name__ == "__main__":
    unittest.main()
