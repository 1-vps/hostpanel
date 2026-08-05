from __future__ import annotations

import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "api-token-foundation.yml"
DOCUMENTATION = ROOT / "API-TOKEN-FOUNDATION.md"


class ApiTokenFoundationWorkflowTests(unittest.TestCase):
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

    def test_workflow_runs_complete_package_contract(self) -> None:
        self.assertIn("tools/hostpanel_api_tokens/*.py", self.source)
        self.assertIn("tests.test_hostpanel_api_tokens", self.source)
        self.assertIn("tests.test_hostpanel_api_token_snapshot", self.source)
        self.assertIn("tests.test_api_token_foundation_workflow", self.source)
        self.assertIn('PRAGMA foreign_keys = ON', self.source)
        self.assertIn('PRAGMA trusted_schema = OFF', self.source)
        self.assertLess(
            self.source.index("Verify exact checkout"),
            self.source.index("Run token policy regressions"),
        )

    def test_path_filters_cover_all_contract_inputs(self) -> None:
        for path in (
            ".github/workflows/api-token-foundation.yml",
            "API-TOKEN-FOUNDATION.md",
            "tools/hostpanel_api_tokens/**",
            "tests/test_hostpanel_api_tokens.py",
            "tests/test_hostpanel_api_token_snapshot.py",
            "tests/test_api_token_foundation_workflow.py",
        ):
            self.assertGreaterEqual(self.source.count(f"- {path}"), 2, path)

    def test_documentation_does_not_claim_api_completion(self) -> None:
        self.assertIn("not expose HTTP routes", self.documentation)
        self.assertIn("Not yet delivered", self.documentation)
        self.assertIn("HTTP 401", self.documentation)
        self.assertIn("HTTP 403", self.documentation)
        self.assertIn("trusted proxy-normalized source address", self.documentation)


if __name__ == "__main__":
    unittest.main()
