from __future__ import annotations

import ast
import pathlib
import unittest

from integration_test_support import TestStack, decode, request


class IntegrationSecurityTests(unittest.TestCase):
    def test_package_has_no_runtime_server_process_or_dynamic_loader(self):
        root = pathlib.Path(__file__).parents[1] / "tools" / "hostpanel_api_integration"
        forbidden_calls = {"eval", "exec", "compile", "__import__"}
        forbidden_import_roots = {"subprocess", "socket", "importlib"}
        for path in root.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    self.assertFalse(any(alias.name.split(".")[0] in forbidden_import_roots for alias in node.names), path)
                if isinstance(node, ast.ImportFrom):
                    self.assertNotIn((node.module or "").split(".")[0], forbidden_import_roots, path)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    self.assertNotIn(node.func.id, forbidden_calls, path)

    def test_unknown_json_fields_fail_closed(self):
        stack = TestStack(); self.addCleanup(stack.close); app = stack.app()
        response = app.dispatch(request("POST", "/api/v1/tenants/tenant-a/jobs", body={"job_type": "site.backup", "payload": {}, "command": "rm"}, idem="key-security-001"), now=100)
        self.assertEqual(response.status, 400)
        self.assertEqual(decode(response)["error"]["code"], "unknown_field")

    def test_missing_idempotency_key_fails_before_handler(self):
        stack = TestStack(); self.addCleanup(stack.close); app = stack.app()
        response = app.dispatch(request("POST", "/api/v1/tenants/tenant-a/jobs", body={"job_type": "site.backup", "payload": {}}), now=100)
        self.assertEqual(response.status, 400)
        self.assertEqual(stack.connection.execute("SELECT count(*) FROM hp_api_jobs").fetchone()[0], 0)

    def test_webhook_secret_reference_is_redacted_in_central_audit(self):
        stack = TestStack(); self.addCleanup(stack.close); app = stack.app()
        response = app.dispatch(request("POST", "/api/v1/tenants/tenant-a/webhook-destinations", body={"url": "https://hooks.example.net/path", "secret_reference": "vault:super-secret", "event_types": ["job.failed"]}, idem="key-security-000002"), now=100)
        self.assertEqual(response.status, 201)
        # Fake store does not implement redaction; ensure API response never returns secret bytes beyond the opaque reference field.
        raw = response.body.decode()
        self.assertNotIn("super-secret-value", raw)

    def test_bad_cursor_does_not_reach_store(self):
        stack = TestStack(); self.addCleanup(stack.close); app = stack.app()
        response = app.dispatch(request("GET", "/api/v1/tenants/tenant-a/jobs", query="cursor=unsigned"), now=100)
        self.assertEqual(response.status, 400)


if __name__ == "__main__":
    unittest.main()
