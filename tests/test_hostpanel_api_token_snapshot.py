from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from hostpanel_api_tokens import (
    Actor,
    ApiTokenStore,
    AuthorizationError,
)


NOW = 1_800_000_000
ACTOR = Actor("system", "snapshot-test")


class ApiTokenSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.store = ApiTokenStore(self.connection)
        self.store.migrate()
        self.account = self.store.create_service_account(
            name="Snapshot automation",
            scopes=("sites:read",),
            tenant_ids=("tenant-a",),
            source_cidrs=("192.0.2.0/24",),
            actor=ACTOR,
            now=NOW,
        )
        self.issued = self.store.issue_token(
            self.account.account_id,
            expires_at=NOW + 3600,
            actor=ACTOR,
            now=NOW,
        )

    def tearDown(self) -> None:
        self.connection.close()

    def test_authentication_uses_one_database_snapshot(self) -> None:
        statements: list[str] = []
        self.connection.set_trace_callback(statements.append)
        principal = self.store.authenticate(
            self.issued.token,
            required_scope="sites:read",
            tenant_id="tenant-a",
            source_ip="192.0.2.1",
            now=NOW + 1,
            touch=False,
        )
        self.connection.set_trace_callback(None)
        self.assertEqual(principal.token_id, self.issued.metadata.token_id)
        self.assertTrue(statements[0].startswith("SAVEPOINT hp_api_"), statements)
        self.assertIn("FROM hp_api_tokens AS t", "\n".join(statements))
        self.assertTrue(statements[-1].startswith("RELEASE hp_api_"), statements)
        self.assertFalse(self.connection.in_transaction)

    def test_failed_authorization_rolls_back_snapshot(self) -> None:
        statements: list[str] = []
        self.connection.set_trace_callback(statements.append)
        with self.assertRaises(AuthorizationError):
            self.store.authenticate(
                self.issued.token,
                required_scope="sites:write",
                tenant_id="tenant-a",
                source_ip="192.0.2.1",
                now=NOW + 1,
                touch=False,
            )
        self.connection.set_trace_callback(None)
        self.assertTrue(
            any(value.startswith("ROLLBACK TO hp_api_") for value in statements),
            statements,
        )
        self.assertTrue(statements[-1].startswith("RELEASE hp_api_"), statements)
        self.assertFalse(self.connection.in_transaction)


if __name__ == "__main__":
    unittest.main()
