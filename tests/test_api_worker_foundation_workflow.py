from __future__ import annotations

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "api-worker-foundation.yml"
DOCUMENTATION = ROOT / "API-WORKER-FOUNDATION.md"


class ApiWorkerFoundationWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")
        cls.documentation = DOCUMENTATION.read_text(encoding="utf-8")

    def test_workflow_is_read_only_exact_head_and_pinned(self):
        self.assertIn("permissions:\n  contents: read", self.workflow)
        self.assertNotIn("pull_request_target", self.workflow)
        self.assertNotIn("secrets.", self.workflow)
        self.assertIn("persist-credentials: false", self.workflow)
        reviewed = "${{ github.event.pull_request.head.sha || github.sha }}"
        self.assertIn(f"ref: {reviewed}", self.workflow)
        self.assertIn(f"EXPECTED_SHA: {reviewed}", self.workflow)
        uses = re.findall(r"(?m)^\s*-?\s*uses:\s*([^\s#]+)", self.workflow)
        self.assertEqual(
            uses,
            ["actions/checkout@11d5960a326750d5838078e36cf38b85af677262"],
        )

    def test_complete_api_prerequisite_chain_runs_before_worker(self):
        step = "Validate complete API prerequisite chain"
        self.assertIn(step, self.workflow)
        for phrase in (
            "tools/validate_release_manifest.py",
            "tests.test_release_manifest",
            "tools/hostpanel_api_tokens/*.py",
            "tests.test_hostpanel_api_tokens",
            "tools/hostpanel_api_control/*.py",
            "tests.test_hostpanel_api_control_core",
            "tools/hostpanel_api_rbac/*.py",
            "tests.test_hostpanel_api_rbac_policy",
            "tools/hostpanel_api_http/*.py",
            "tests.test_hostpanel_api_http_routing",
            "tests.test_hostpanel_api_http_security",
            "tests.test_hostpanel_api_http_rate_limit",
            "tests.test_hostpanel_api_http_openapi",
            "tests.test_hostpanel_api_http_wsgi",
            "Verify prerequisite OpenAPI is deterministic",
        ):
            self.assertIn(phrase, self.workflow)
        self.assertLess(
            self.workflow.index(step),
            self.workflow.index("Run worker regressions"),
        )

    def test_workflow_runs_all_regressions_and_real_store_integration(self):
        for phrase in (
            "tools/hostpanel_api_worker/*.py",
            "tests.test_hostpanel_api_worker_registry",
            "tests.test_hostpanel_api_worker_runner",
            "tests.test_hostpanel_api_worker_failures",
            "tests.test_hostpanel_api_worker_service",
            "tests.test_hostpanel_api_worker_compatibility",
            "tests.test_api_worker_foundation_workflow",
            "from hostpanel_api_control import ApiControlStore",
            "store.migrate()",
            "store.create_job(",
            "outcome.status != \"succeeded\"",
            "persisted[0].status != \"succeeded\"",
        ):
            self.assertIn(phrase, self.workflow)

    def test_path_filters_cover_every_layer(self):
        for path in (
            "RELEASE-MANIFEST.json",
            "tools/validate_release_manifest.py",
            "tools/hostpanel_api_tokens/**",
            "tools/hostpanel_api_control/**",
            "tools/hostpanel_api_rbac/**",
            "tools/hostpanel_api_http/**",
            "tools/generate_hostpanel_openapi.py",
            ".github/workflows/api-worker-foundation.yml",
            "API-WORKER-FOUNDATION.md",
            "tools/hostpanel_api_worker/**",
            "tests/worker_test_support.py",
            "tests/test_hostpanel_api_worker_registry.py",
            "tests/test_hostpanel_api_worker_runner.py",
            "tests/test_hostpanel_api_worker_failures.py",
            "tests/test_hostpanel_api_worker_service.py",
            "tests/test_hostpanel_api_worker_compatibility.py",
            "tests/test_api_worker_foundation_workflow.py",
        ):
            self.assertGreaterEqual(self.workflow.count(f"- {path}"), 2, path)

    def test_dynamic_loading_and_process_surfaces_are_forbidden(self):
        for phrase in (
            '"importlib"',
            '"subprocess"',
            '"os.system"',
            '"eval("',
            '"exec("',
            '"serve_forever"',
            '"systemctl"',
        ):
            self.assertIn(phrase, self.workflow)
        self.assertIn("fixed-registry security boundary", self.workflow)

    def test_documentation_keeps_delivery_boundary_explicit(self):
        for phrase in (
            "does not install or start a system service",
            "cooperative lease heartbeat",
            "never be selected from a module path",
            "lease_lost",
            "Not yet delivered",
            "does not complete #143",
            "billing blocker #13",
        ):
            self.assertIn(phrase, self.documentation)


if __name__ == "__main__":
    unittest.main()
