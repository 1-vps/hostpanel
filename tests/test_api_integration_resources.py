from __future__ import annotations

import unittest

from integration_test_support import TestStack, decode, request


class ResourceIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.stack = TestStack()
        self.addCleanup(self.stack.close)
        self.app = self.stack.app()

    def _create_job(self):
        response = self.app.dispatch(request("POST", "/api/v1/tenants/tenant-a/jobs", body={"job_type": "site.backup", "payload": {}}, idem="key-job-resource1"), now=100)
        return decode(response)["job"]["job_id"]

    def test_job_list_get_cancel_and_logs(self):
        job_id = self._create_job()
        listed = self.app.dispatch(request("GET", "/api/v1/tenants/tenant-a/jobs"), now=101)
        self.assertEqual(decode(listed)["jobs"][0]["job_id"], job_id)
        fetched = self.app.dispatch(request("GET", f"/api/v1/tenants/tenant-a/jobs/{job_id}"), now=101)
        self.assertEqual(decode(fetched)["job"]["job_id"], job_id)
        logs = self.app.dispatch(request("GET", f"/api/v1/tenants/tenant-a/jobs/{job_id}/logs", query="limit=2"), now=101)
        self.assertEqual(len(decode(logs)["logs"]), 2)
        cancelled = self.app.dispatch(request("POST", f"/api/v1/tenants/tenant-a/jobs/{job_id}/cancel", idem="key-cancel-00001"), now=102)
        self.assertEqual(decode(cancelled)["job"]["status"], "cancelled")

    def test_service_account_list_is_tenant_bound(self):
        created = self.app.dispatch(request(
            "POST", "/api/v1/tenants/tenant-a/service-accounts",
            body={"name": "tenant-a account", "scopes": ["job:read"]},
            idem="key-list-account01",
        ), now=100)
        self.assertEqual(created.status, 201)
        listing = self.app.dispatch(request(
            "GET", "/api/v1/tenants/tenant-a/service-accounts",
            query="limit=10",
        ), now=101)
        self.assertEqual(listing.status, 200)
        self.assertEqual(len(decode(listing)["service_accounts"]), 1)
        other = self.app.dispatch(request(
            "GET", "/api/v1/tenants/tenant-b/service-accounts",
            query="limit=10",
        ), now=101)
        self.assertEqual(decode(other)["service_accounts"], [])

    def test_webhook_destination_lifecycle_and_tenant_binding(self):
        created = self.app.dispatch(request(
            "POST", "/api/v1/tenants/tenant-a/webhook-destinations",
            body={"url": "https://hooks.example/path", "secret_reference": "vault:item", "event_types": ["job.succeeded"]},
            idem="key-webhook-000001",
        ), now=100)
        self.assertEqual(created.status, 201)
        destination = decode(created)["destination"]
        self.assertNotIn("secret_reference", destination)
        self.assertTrue(destination["secret_configured"])
        destination_id = destination["destination_id"]
        read = self.app.dispatch(request("GET", f"/api/v1/tenants/tenant-a/webhook-destinations/{destination_id}"), now=101)
        self.assertEqual(read.status, 200)
        cross = self.app.dispatch(request("GET", f"/api/v1/tenants/tenant-b/webhook-destinations/{destination_id}"), now=101)
        self.assertEqual(cross.status, 404)
        updated = self.app.dispatch(request(
            "PUT", f"/api/v1/tenants/tenant-a/webhook-destinations/{destination_id}",
            body={"url": "https://hooks.example/new", "secret_reference": "vault:new", "event_types": ["job.failed"], "expected_version": 1},
            idem="key-webhook-000002",
        ), now=102)
        self.assertEqual(decode(updated)["destination"]["version"], 2)
        disabled = self.app.dispatch(request(
            "DELETE", f"/api/v1/tenants/tenant-a/webhook-destinations/{destination_id}",
            body={"expected_version": 2}, idem="key-webhook-000003",
        ), now=103)
        self.assertFalse(decode(disabled)["destination"]["enabled"])

    def test_policy_simulation_is_audited_for_allow_and_deny(self):
        allowed = self.app.dispatch(request("POST", "/api/v1/tenants/tenant-a/policy-simulations", body={"capability": "job:read"}), now=100)
        self.assertTrue(decode(allowed)["decision"]["allowed"])
        self.stack.rbac.allowed = False
        # Access itself needs policy:simulate; permit that call by using a separate fake result toggle after access.
        original = self.stack.rbac.authorize
        self.stack.rbac.authorize = lambda **kwargs: original(**kwargs) if False else type("D", (), {"allowed": True})()
        denied = self.app.dispatch(request("POST", "/api/v1/tenants/tenant-a/policy-simulations", body={"capability": "job:read"}), now=101)
        self.assertFalse(decode(denied)["decision"]["allowed"])
        self.assertEqual(self.stack.audit.events[-1].outcome, "denied")

    def test_audit_search_get_and_exports(self):
        self._create_job()
        search = self.app.dispatch(request("GET", "/api/v1/tenants/tenant-a/audit-events", query="limit=10"), now=101)
        self.assertEqual(search.status, 200)
        event_id = decode(search)["events"][0]["event_id"]
        read = self.app.dispatch(request("GET", f"/api/v1/tenants/tenant-a/audit-events/{event_id}"), now=101)
        self.assertEqual(decode(read)["event"]["event_id"], event_id)
        exported = self.app.dispatch(request("POST", "/api/v1/tenants/tenant-a/audit-exports", body={"format": "jsonl"}, idem="key-audit-export1"), now=102)
        self.assertEqual(exported.status, 201)
        payload = decode(exported)
        self.assertEqual(payload["format"], "jsonl")
        self.assertEqual(len(payload["sha256"]), 64)

    def test_cursor_is_bound_to_principal_and_tenant(self):
        for index in range(3):
            self.app.dispatch(request("POST", "/api/v1/tenants/tenant-a/jobs", body={"job_type": "site.backup", "payload": {"n": index}}, idem=f"key-page-{index:08d}"), now=100 + index)
        first = self.app.dispatch(request("GET", "/api/v1/tenants/tenant-a/jobs", query="limit=1"), now=200)
        cursor = decode(first)["next_cursor"]
        self.assertIsNotNone(cursor)
        second = self.app.dispatch(request("GET", "/api/v1/tenants/tenant-a/jobs", query="limit=1&cursor=" + cursor), now=200)
        self.assertEqual(second.status, 200)
        wrong_tenant = self.app.dispatch(request("GET", "/api/v1/tenants/tenant-b/jobs", query="limit=1&cursor=" + cursor), now=200)
        self.assertEqual(wrong_tenant.status, 400)


if __name__ == "__main__":
    unittest.main()
