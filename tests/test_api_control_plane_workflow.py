from __future__ import annotations

import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "api-control-plane-foundation.yml"
DOCUMENTATION = ROOT / "API-CONTROL-PLANE-FOUNDATION.md"


class ApiControlPlaneWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = WORKFLOW.read_text(encoding="utf-8")
        cls.documentation = DOCUMENTATION.read_text(encoding="utf-8")

    def test_workflow_is_read_only_and_secret_free(self) -> None:
        self.assertIn("permissions:\n  contents: read", self.source)
        self.assertNotIn("pull_request_target", self.source)
        self.assertNotIn("secrets.", self.source)
        self.assertIn("persist-credentials: false", self.source)
        self.assertNotRegex(
            self.source,
            r"(?m)^\s+(?:actions|checks|deployments|issues|packages|pull-requests|statuses):\s*write\s*$",
        )

    def test_checkout_is_exact_head_and_action_is_pinned(self) -> None:
        reviewed = "${{ github.event.pull_request.head.sha || github.sha }}"
        self.assertIn(f"ref: {reviewed}", self.source)
        self.assertIn(f"EXPECTED_SHA: {reviewed}", self.source)
        self.assertIn('actual_sha="$(git rev-parse HEAD)"', self.source)
        uses = re.findall(r"(?m)^\s*-?\s*uses:\s*([^\s#]+)", self.source)
        self.assertEqual(
            uses,
            ["actions/checkout@11d5960a326750d5838078e36cf38b85af677262"],
        )

    def test_workflow_runs_complete_control_plane_contract(self) -> None:
        self.assertIn("tools/hostpanel_api_control/*.py", self.source)
        self.assertIn("tests.test_hostpanel_api_control_core", self.source)
        self.assertIn("tests.test_hostpanel_api_control_outbox", self.source)
        self.assertIn("tests.test_hostpanel_api_control_security", self.source)
        self.assertIn("tests.test_hostpanel_api_control_concurrency", self.source)
        self.assertIn("tests.test_api_control_plane_workflow", self.source)
        self.assertIn('PRAGMA foreign_keys = ON', self.source)
        self.assertIn('PRAGMA trusted_schema = OFF', self.source)
        self.assertIn("sqlite3.connect(database, timeout=5)", self.source)
        self.assertLess(
            self.source.index("Verify exact checkout"),
            self.source.index("Run control-plane regressions"),
        )

    def test_path_filters_cover_every_contract_input(self) -> None:
        for path in (
            ".github/workflows/api-control-plane-foundation.yml",
            "API-CONTROL-PLANE-FOUNDATION.md",
            "tools/hostpanel_api_control/**",
            "tests/api_control_test_support.py",
            "tests/test_hostpanel_api_control_core.py",
            "tests/test_hostpanel_api_control_outbox.py",
            "tests/test_hostpanel_api_control_security.py",
            "tests/test_hostpanel_api_control_concurrency.py",
            "tests/test_api_control_plane_workflow.py",
        ):
            self.assertGreaterEqual(self.source.count(f"- {path}"), 2, path)

    def test_documentation_keeps_the_delivery_boundary_explicit(self) -> None:
        self.assertIn("does not expose HTTP routes", self.documentation)
        self.assertIn("Not yet delivered", self.documentation)
        self.assertIn("permanent destination-scoped dedupe key", self.documentation)
        self.assertIn("caller-owned outer transaction", self.documentation)
        self.assertIn("A job payload is data, never a", self.documentation)
        self.assertIn("does not complete #143", self.documentation)


if __name__ == "__main__":
    unittest.main()
