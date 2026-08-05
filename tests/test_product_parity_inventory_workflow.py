from __future__ import annotations

import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "product-parity-inventory.yml"


class ProductParityInventoryWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = WORKFLOW.read_text(encoding="utf-8")

    def test_workflow_is_read_only_and_does_not_use_secrets(self) -> None:
        self.assertIn("permissions:\n  contents: read", self.source)
        self.assertNotIn("pull_request_target", self.source)
        self.assertNotIn("secrets.", self.source)
        self.assertNotRegex(
            self.source,
            r"(?m)^\s+(?:actions|checks|deployments|issues|packages|pull-requests|statuses):\s*write\s*$",
        )
        self.assertIn("persist-credentials: false", self.source)

    def test_external_actions_are_immutable_and_reviewed(self) -> None:
        uses = re.findall(r"(?m)^\s*-?\s*uses:\s*([^\s#]+)", self.source)
        self.assertEqual(
            uses,
            [
                "actions/checkout@11d5960a326750d5838078e36cf38b85af677262",
                "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
            ],
        )
        for action in uses:
            self.assertRegex(action, r"@[0-9a-f]{40}$")

    def test_exact_head_is_checked_before_inventory(self) -> None:
        reviewed = "${{ github.event.pull_request.head.sha || github.sha }}"
        self.assertIn(f"ref: {reviewed}", self.source)
        self.assertIn(f"EXPECTED_SHA: {reviewed}", self.source)
        self.assertIn('actual_sha="$(git rev-parse HEAD)"', self.source)
        self.assertLess(
            self.source.index("Verify exact checkout"),
            self.source.index("Inventory the signed application source"),
        )

    def test_workflow_runs_real_archive_scan_and_validates_evidence(self) -> None:
        self.assertIn("python3 tools/audit_product_parity.py", self.source)
        self.assertIn("--output artifacts/product-parity/inventory.json", self.source)
        self.assertIn("tests.test_product_parity_inventory", self.source)
        self.assertIn("tests.test_product_parity_inventory_workflow", self.source)
        self.assertIn("sha256sum --check inventory.sha256", self.source)
        self.assertIn("inventory did not scan the signed application", self.source)
        self.assertIn(
            "if find artifacts/product-parity -xdev -type l -print -quit | grep -q .; then",
            self.source,
        )
        self.assertNotIn("inventory evidence contains a symlink' >&2; exit 1; } || true", self.source)
        self.assertIn("if-no-files-found: error", self.source)
        self.assertIn("retention-days: 14", self.source)

    def test_path_filters_cover_every_inventory_input(self) -> None:
        for path in (
            ".github/workflows/product-parity-inventory.yml",
            "tools/audit_product_parity.py",
            "tests/test_product_parity_inventory.py",
            "tests/test_product_parity_inventory_workflow.py",
            "SHA256SUMS",
            "'hostpanel-*-source.tar.gz'",
        ):
            self.assertGreaterEqual(self.source.count(f"- {path}"), 2, path)


if __name__ == "__main__":
    unittest.main()
