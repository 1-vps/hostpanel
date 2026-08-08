from __future__ import annotations

import sqlite3
import unittest

from integration_test_support import FakeAuditStore, FakeControlStore, FakeRbacStore, FakeTokenStore, FakeWebhookStore, TestStack, decode, install_domain_stubs


class CliAndSchemaTests(unittest.TestCase):
    def setUp(self):
        self.stack = TestStack()
        self.addCleanup(self.stack.close)
        self.app = self.stack.app()

    def test_cli_dispatches_through_same_route_table(self):
        from hostpanel_api_integration import ApiCli
        cli = ApiCli(self.app)
        result = cli.execute([
            "POST", "/api/v1/tenants/tenant-a/jobs",
            "--token", "good-token-0000000000",
            "--idempotency-key", "key-cli-00000001",
            "--body", '{"job_type":"site.backup","payload":{}}',
        ], now=100)
        self.assertEqual(result.exit_code, 0)
        self.assertIn('"job"', result.stdout)
        self.assertEqual(self.stack.connection.execute("SELECT count(*) FROM hp_api_jobs").fetchone()[0], 1)

    def test_cli_invalid_json_does_not_dispatch(self):
        from hostpanel_api_integration import ApiCli
        result = ApiCli(self.app).execute(["POST", "/api/v1/tenants/tenant-a/jobs", "--body", "{"], now=100)
        self.assertEqual(result.exit_code, 64)
        self.assertIn("valid JSON", result.stderr)

    def test_bundle_requires_one_connection(self):
        from hostpanel_api_integration import StoreBundle
        install_domain_stubs()
        left = sqlite3.connect(":memory:")
        right = sqlite3.connect(":memory:")
        self.addCleanup(left.close)
        self.addCleanup(right.close)
        for connection in (left, right):
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA trusted_schema = OFF")
        trace = []
        with self.assertRaises(Exception):
            StoreBundle(FakeTokenStore(left, trace), FakeControlStore(right, trace), FakeRbacStore(left, trace), FakeWebhookStore(left), FakeAuditStore(left))

    def test_migrate_all_preserves_outer_transaction_rollback(self):
        bundle = self.stack.bundle()
        self.stack.connection.execute("BEGIN")
        bundle.migrate_all()
        self.stack.connection.execute("CREATE TABLE outer_marker(value INTEGER)")
        self.stack.connection.rollback()
        marker = self.stack.connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='outer_marker'").fetchone()
        self.assertIsNone(marker)

    def test_route_table_is_frozen(self):
        from hostpanel_api_http import Route
        with self.assertRaises(Exception):
            self.app.register(Route(method="GET", path="/api/v1/extra", operation_id="getExtra", summary="Extra", auth_required=False, handler=lambda ctx: {}))

    def test_query_parameter_contract_rejects_duplicates(self):
        from hostpanel_api_http import Route
        with self.assertRaises(Exception):
            Route(
                method="GET", path="/api/v1/example", operation_id="getExample", summary="Example",
                auth_required=False, handler=lambda ctx: {},
                query_parameters=(
                    {"in": "query", "name": "limit", "schema": {"type": "integer"}},
                    {"in": "query", "name": "limit", "schema": {"type": "integer"}},
                ),
            )


if __name__ == "__main__":
    unittest.main()
