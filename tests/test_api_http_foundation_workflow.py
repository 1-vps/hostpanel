from __future__ import annotations

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "api-http-foundation.yml"
DOCUMENTATION = ROOT / "API-HTTP-FOUNDATION.md"


class ApiHttpFoundationWorkflowTests(unittest.TestCase):
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

    def test_workflow_runs_complete_transport_contract(self) -> None:
        for phrase in (
            "tools/hostpanel_api_http/*.py",
            "tests.test_hostpanel_api_http_routing",
            "tests.test_hostpanel_api_http_security",
            "tests.test_hostpanel_api_http_rate_limit",
            "tests.test_hostpanel_api_http_openapi",
            "tests.test_hostpanel_api_http_wsgi",
            "tests.test_api_http_foundation_workflow",
            "python3 tools/generate_hostpanel_openapi.py",
            "Verify generated OpenAPI is deterministic",
            'cmp "$first" "$second"',
            'python3 -m json.tool "$first"',
            "PRAGMA foreign_keys = ON",
            "PRAGMA trusted_schema = OFF",
            "sqlite3.connect(database, timeout=5)",
        ):
            self.assertIn(phrase, self.source)
        self.assertLess(
            self.source.index("Verify exact checkout"),
            self.source.index("Run API HTTP regressions"),
        )

    def test_path_filters_cover_every_contract_input(self) -> None:
        for path in (
            ".github/workflows/api-http-foundation.yml",
            "API-HTTP-FOUNDATION.md",
            "tools/generate_hostpanel_openapi.py",
            "tools/hostpanel_api_http/**",
            "tests/http_test_support.py",
            "tests/test_hostpanel_api_http_routing.py",
            "tests/test_hostpanel_api_http_security.py",
            "tests/test_hostpanel_api_http_security_requests.py",
            "tests/test_hostpanel_api_http_security_cursor.py",
            "tests/test_hostpanel_api_http_rate_limit.py",
            "tests/test_hostpanel_api_http_openapi.py",
            "tests/test_hostpanel_api_http_wsgi.py",
            "tests/test_api_http_foundation_workflow.py",
        ):
            self.assertGreaterEqual(self.source.count(f"- {path}"), 2, path)

    def test_documentation_and_generated_contract_keep_boundaries_explicit(self) -> None:
        for phrase in (
            "does not mount routes into the signed HostPanel",
            "access controller owns the order",
            "Idempotency-Key",
            "does not complete #143",
            "Not yet delivered",
            "never opens a socket or starts a server",
        ):
            self.assertIn(phrase, self.documentation)
        self.assertIn("generated deterministically on demand", self.documentation)
        self.assertIn("generates it twice", self.documentation)


if __name__ == "__main__":
    unittest.main()
