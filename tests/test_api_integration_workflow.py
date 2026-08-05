from __future__ import annotations

import pathlib
import unittest


class IntegrationWorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = pathlib.Path(__file__).parents[1]
        cls.workflow = (root / ".github/workflows/api-integration-foundation.yml").read_text(encoding="utf-8")
        cls.documentation = (root / "API-INTEGRATION-FOUNDATION.md").read_text(encoding="utf-8")
        cls.smoke = (root / "tests/api_integration_exact_stack_smoke.py").read_text(encoding="utf-8")

    def test_workflow_is_exact_head_and_read_only(self):
        self.assertIn("github.event.pull_request.head.sha", self.workflow)
        self.assertIn("persist-credentials: false", self.workflow)
        self.assertIn("permissions:\n  contents: read", self.workflow)

    def test_workflow_runs_local_regressions_and_actual_stack_smoke(self):
        self.assertIn("test_hostpanel_api_*.py", self.workflow)
        self.assertIn("test_api_*_workflow.py", self.workflow)
        self.assertIn("test_api_integration_*.py", self.workflow)
        self.assertIn("tests/api_integration_exact_stack_smoke.py", self.workflow)
        for name in (
            "ApiTokenStore", "ApiControlStore", "ApiRbacStore",
            "WebhookDestinationStore", "ApiAuditStore", "ManagementApi", "ApiCli",
        ):
            self.assertIn(name, self.smoke)
        self.assertIn("authenticate_identity", self.smoke)
        self.assertIn("b'hpv1.' not in bytes(persisted)", self.smoke)
        self.assertIn("verification = stores.audit.verify_chain()", self.smoke)
        self.assertIn("verification.last_sequence == verification.event_count", self.smoke)

    def test_workflow_has_no_dependency_install_or_runtime_start(self):
        lowered = self.workflow.lower()
        self.assertNotIn("pip install", lowered)
        self.assertNotIn("apt-get", lowered)
        self.assertNotIn("systemctl", lowered)
        self.assertNotIn("docker", lowered)

    def test_documentation_states_external_blockers_without_claiming_completion(self):
        for value in ("#13", "#151", "signed HostPanel runtime", "production VM"):
            self.assertIn(value, self.documentation)
        self.assertIn("does not claim completion", self.documentation)


if __name__ == "__main__":
    unittest.main()
