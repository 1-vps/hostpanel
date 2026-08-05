from __future__ import annotations

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "api-webhook-delivery-foundation.yml"
DOCUMENTATION = ROOT / "API-WEBHOOK-DELIVERY-FOUNDATION.md"


class ApiWebhookDeliveryWorkflowTests(unittest.TestCase):
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

    def test_complete_prerequisite_chain_runs_before_webhooks(self):
        step = "Validate complete release and API prerequisite chain"
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
            "tools/hostpanel_api_worker/*.py",
            "tests.test_hostpanel_api_worker_registry",
            "Verify prerequisite OpenAPI is deterministic",
        ):
            self.assertIn(phrase, self.workflow)
        self.assertLess(
            self.workflow.index(step),
            self.workflow.index("Run webhook regressions"),
        )

    def test_workflow_runs_all_regressions_and_actual_stack_integration(self):
        for phrase in (
            "tools/hostpanel_api_webhooks/*.py",
            "tests.test_hostpanel_api_webhook_url_policy",
            "tests.test_hostpanel_api_webhook_store",
            "tests.test_hostpanel_api_webhook_delivery",
            "tests.test_hostpanel_api_webhook_security",
            "tests.test_api_webhook_delivery_workflow",
            "from hostpanel_api_control import ApiControlStore",
            "from hostpanel_api_control.signing import sign_webhook",
            "control.enqueue_outbox_event(",
            'outcome.status != "delivered"',
            'persisted[0].status != "delivered"',
            "transport.requests[0].allow_redirects",
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
            "tools/hostpanel_api_worker/**",
            ".github/workflows/api-webhook-delivery-foundation.yml",
            "API-WEBHOOK-DELIVERY-FOUNDATION.md",
            "tools/hostpanel_api_webhooks/**",
            "tests/webhook_test_support.py",
            "tests/test_hostpanel_api_webhook_*.py",
            "tests/test_api_webhook_delivery_workflow.py",
        ):
            self.assertGreaterEqual(self.workflow.count(f"- {path}"), 2, path)

    def test_network_and_process_surfaces_are_forbidden(self):
        for phrase in (
            '"http.client"',
            '"urllib.request"',
            '"requests"',
            '"socket.create_connection"',
            '"importlib"',
            '"subprocess"',
            '"os.system"',
            '"eval("',
            '"exec("',
            '"serve_forever"',
            '"systemctl"',
        ):
            self.assertIn(phrase, self.workflow)

    def test_documentation_keeps_transport_and_delivery_boundaries_explicit(self):
        for phrase in (
            "secret references only",
            "every resolved address is globally routable",
            "peer belongs to the reported DNS result",
            "does not include a production network client",
            "no redirects",
            "does not complete #143",
            "billing blocker #13",
        ):
            self.assertIn(phrase, self.documentation)


if __name__ == "__main__":
    unittest.main()
