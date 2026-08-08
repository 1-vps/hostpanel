from __future__ import annotations

import unittest

from integration_test_support import TestStack, decode, request


class MutationIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.stack = TestStack()
        self.addCleanup(self.stack.close)
        self.app = self.stack.app()

    def test_service_account_creation_builds_token_and_rbac_state_atomically(self):
        response = self.app.dispatch(request(
            "POST", "/api/v1/tenants/tenant-a/service-accounts",
            body={"name": "deploy", "scopes": ["job:create", "job:read"]}, idem="key-service-0001",
        ), now=100)
        self.assertEqual(response.status, 201)
        payload = decode(response)
        account = payload["service_account"]["account_id"]
        self.assertIsNotNone(self.stack.connection.execute("SELECT 1 FROM hp_api_service_accounts WHERE account_id = ?", (account,)).fetchone())
        self.assertIsNotNone(self.stack.connection.execute("SELECT 1 FROM hp_api_principals WHERE principal_id = ?", (account,)).fetchone())
        self.assertEqual(self.stack.audit.events[-1].action, "service_account.created")

    def test_service_account_creation_rolls_back_on_binding_failure(self):
        self.stack.rbac.fail_bind = True
        response = self.app.dispatch(request(
            "POST", "/api/v1/tenants/tenant-a/service-accounts",
            body={"name": "deploy", "scopes": ["job:create"]}, idem="key-service-0002",
        ), now=100)
        self.assertEqual(response.status, 409)
        self.assertEqual(self.stack.connection.execute("SELECT count(*) FROM hp_api_service_accounts WHERE account_id LIKE 'sa_test%'").fetchone()[0], 0)
        self.assertEqual(self.stack.connection.execute("SELECT count(*) FROM hp_api_principals WHERE principal_id LIKE 'sa_test%'").fetchone()[0], 0)
        self.assertEqual(len(self.stack.audit.events), 0)

    def test_job_idempotency_replays_without_duplicate_side_effects(self):
        req = request(
            "POST", "/api/v1/tenants/tenant-a/jobs",
            body={"job_type": "site.backup", "payload": {"site_id": "site-1"}}, idem="key-job-00000001",
        )
        first = self.app.dispatch(req, now=100)
        second = self.app.dispatch(req, now=101)
        self.assertEqual(first.status, 201)
        self.assertEqual(second.status, 201)
        self.assertEqual(decode(first), decode(second))
        self.assertIn(("Idempotency-Replayed", "true"), second.headers)
        self.assertEqual(self.stack.connection.execute("SELECT count(*) FROM hp_api_jobs").fetchone()[0], 1)
        self.assertEqual(sum(event.action == "job.created" for event in self.stack.audit.events), 1)

    def test_idempotency_key_reuse_with_different_body_conflicts(self):
        first = request("POST", "/api/v1/tenants/tenant-a/jobs", body={"job_type": "site.backup", "payload": {}}, idem="key-job-00000002")
        second = request("POST", "/api/v1/tenants/tenant-a/jobs", body={"job_type": "site.deploy", "payload": {}}, idem="key-job-00000002")
        self.assertEqual(self.app.dispatch(first, now=100).status, 201)
        self.assertEqual(self.app.dispatch(second, now=101).status, 409)

    def test_domain_failure_rolls_back_insert_and_pending_idempotency(self):
        self.stack.control.fail_after_job_insert = True
        response = self.app.dispatch(request(
            "POST", "/api/v1/tenants/tenant-a/jobs",
            body={"job_type": "site.backup", "payload": {}}, idem="key-job-00000003",
        ), now=100)
        self.assertEqual(response.status, 409)
        self.assertEqual(self.stack.connection.execute("SELECT count(*) FROM hp_api_jobs").fetchone()[0], 0)

    def test_unregistered_job_type_is_rejected_before_persistence(self):
        response = self.app.dispatch(request(
            "POST", "/api/v1/tenants/tenant-a/jobs",
            body={"job_type": "shell.exec", "payload": {}}, idem="key-job-00000004",
        ), now=100)
        self.assertEqual(response.status, 400)
        self.assertEqual(self.stack.connection.execute("SELECT count(*) FROM hp_api_jobs").fetchone()[0], 0)

    def test_token_secret_returned_once_and_replay_is_same_response(self):
        create = self.app.dispatch(request(
            "POST", "/api/v1/tenants/tenant-a/service-accounts",
            body={"name": "deploy", "scopes": ["token:issue"]}, idem="key-service-0003",
        ), now=100)
        account_id = decode(create)["service_account"]["account_id"]
        req = request(
            "POST", f"/api/v1/tenants/tenant-a/service-accounts/{account_id}/tokens",
            body={"expires_at": 10000}, idem="key-token-0000001",
        )
        first = self.app.dispatch(req, now=101)
        second = self.app.dispatch(req, now=102)
        self.assertEqual(first.status, 201)
        self.assertTrue(decode(first)["token"].startswith("one-time-"))
        self.assertTrue(decode(first)["secret_available"])
        self.assertNotIn("token", decode(second))
        self.assertFalse(decode(second)["secret_available"])
        issued_records = [
            item for item in self.stack.control.idempotency.values()
            if item.response_body and b'"metadata"' in item.response_body
        ]
        self.assertTrue(issued_records)
        self.assertNotIn(b"one-time-", issued_records[-1].response_body)
        self.assertEqual(self.stack.connection.execute("SELECT count(*) FROM hp_api_tokens WHERE account_id = ?", (account_id,)).fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
