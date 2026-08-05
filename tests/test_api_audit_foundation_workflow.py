from __future__ import annotations

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "api-audit-foundation.yml"
DOCUMENTATION = ROOT / "API-AUDIT-FOUNDATION.md"


class ApiAuditWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")
        cls.documentation = DOCUMENTATION.read_text(encoding="utf-8")

    def test_workflow_is_read_only_exact_head_and_pinned(self):
        self.assertIn("permissions:\n  contents: read", self.workflow)
        self.assertNotIn("pull_request_target", self.workflow)
        self.assertNotIn("secrets.", self.workflow)
        reviewed = "${{ github.event.pull_request.head.sha || github.sha }}"
        self.assertIn(f"ref: {reviewed}", self.workflow)
        self.assertIn(f"EXPECTED_SHA: {reviewed}", self.workflow)
        uses = re.findall(r"(?m)^\s*-?\s*uses:\s*([^\s#]+)", self.workflow)
        self.assertEqual(uses, ["actions/checkout@11d5960a326750d5838078e36cf38b85af677262"])

    def test_path_filters_cover_every_contract_input_twice(self):
        paths = (
            ".github/workflows/api-audit-foundation.yml",
            "API-AUDIT-FOUNDATION.md",
            "tools/hostpanel_api_audit/**",
            "tests/audit_test_support.py",
            "tests/test_hostpanel_api_audit_append.py",
            "tests/test_hostpanel_api_audit_query.py",
            "tests/test_hostpanel_api_audit_export.py",
            "tests/test_hostpanel_api_audit_security.py",
            "tests/test_hostpanel_api_audit_retention.py",
            "tests/test_api_audit_foundation_workflow.py",
        )
        for path in paths:
            self.assertGreaterEqual(self.workflow.count(f"- {path}"), 2, path)

    def test_workflow_runs_all_regressions_and_stacked_smoke(self):
        for phrase in (
            "tests.test_hostpanel_api_audit_append",
            "tests.test_hostpanel_api_audit_query",
            "tests.test_hostpanel_api_audit_export",
            "tests.test_hostpanel_api_audit_security",
            "tests.test_hostpanel_api_audit_retention",
            "from hostpanel_api_audit import ApiAuditStore",
            'action="token.revoked"',
            'action="impersonation.action_denied"',
            'action="job.failed"',
            'action="webhook.delivered"',
            "audit.verify_chain()",
            "audit.export_jsonl(events)",
            "audit.retention_manifest(candidates)",
        ):
            self.assertIn(phrase, self.workflow)

    def test_workflow_forbids_mutation_network_and_process_surfaces(self):
        for phrase in (
            '"subprocess"', '"socket."', '"urllib"', '"requests"',
            '"importlib"', '"os.system"', '"def delete_event"',
            '"def purge"', '"DELETE FROM hp_api_audit_events"',
        ):
            self.assertIn(phrase, self.workflow)

    def test_documentation_states_integrity_retention_and_remaining_boundaries(self):
        for phrase in (
            "tail deletion",
            "externally anchor",
            "never deletes or mutates audit events",
            "metadata allowlists",
            "tenant-required exact filters",
            "does not complete #143",
            "billing blocker #13",
        ):
            self.assertIn(phrase, self.documentation)


if __name__ == "__main__":
    unittest.main()
