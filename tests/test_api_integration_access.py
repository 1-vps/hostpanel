from __future__ import annotations

import json
import unittest

from integration_test_support import TestStack, decode, request


class AccessIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.stack = TestStack()
        self.addCleanup(self.stack.close)
        self.app = self.stack.app()

    def test_access_order_is_identity_then_target_then_token_then_rbac(self):
        response = self.app.dispatch(request("GET", "/api/v1/tenants/tenant-a/jobs"), now=100)
        self.assertEqual(response.status, 200)
        self.assertEqual(self.stack.trace, ["identity", "token-authorize", "rbac"])

    def test_invalid_token_is_401_before_target_lookup(self):
        response = self.app.dispatch(request("GET", "/api/v1/tenants/missing/jobs", token="bad-token-00000000000"), now=100)
        self.assertEqual(response.status, 401)
        self.assertEqual(decode(response)["error"]["code"], "invalid_token")
        self.assertEqual(self.stack.trace, ["identity"])

    def test_missing_tenant_is_404_after_identity(self):
        response = self.app.dispatch(request("GET", "/api/v1/tenants/missing/jobs"), now=100)
        self.assertEqual(response.status, 404)
        self.assertEqual(self.stack.trace, ["identity"])

    def test_token_scope_denial_is_403(self):
        self.stack.tokens.allowed_scopes = {"audit:read"}
        response = self.app.dispatch(request("GET", "/api/v1/tenants/tenant-a/jobs"), now=100)
        self.assertEqual(response.status, 403)
        self.assertEqual(self.stack.trace, ["identity", "token-authorize"])

    def test_rbac_denial_is_403(self):
        self.stack.rbac.allowed = False
        response = self.app.dispatch(request("GET", "/api/v1/tenants/tenant-a/jobs"), now=100)
        self.assertEqual(response.status, 403)
        self.assertEqual(self.stack.trace, ["identity", "token-authorize", "rbac"])

    def test_cross_tenant_job_is_hidden_as_404(self):
        self.stack.control.create_job(tenant_id="tenant-b", job_type="site.backup", payload={}, now=1)
        job_id = self.stack.control.list_jobs(tenant_id="tenant-b")[0].job_id
        response = self.app.dispatch(request("GET", f"/api/v1/tenants/tenant-a/jobs/{job_id}"), now=100)
        self.assertEqual(response.status, 404)

    def test_request_context_uses_dispatch_timestamp(self):
        response = self.app.dispatch(request("POST", "/api/v1/tenants/tenant-a/policy-simulations", body={"capability": "job:read"}), now=12345)
        self.assertEqual(response.status, 200)
        self.assertEqual(self.stack.audit.events[-1].occurred_at, 12345)

    def test_openapi_includes_query_parameters(self):
        response = self.app.dispatch(request("GET", "/api/v1/openapi.json", token=""), now=100)
        self.assertEqual(response.status, 200)
        document = decode(response)
        params = document["paths"]["/api/v1/tenants/{tenant_id}/jobs"]["get"]["parameters"]
        names = {param.get("name") for param in params if isinstance(param, dict)}
        self.assertTrue({"cursor", "limit", "status"}.issubset(names))


if __name__ == "__main__":
    unittest.main()
