from __future__ import annotations

import sqlite3
import unittest

from tests.audit_test_support import *  # noqa: F403


class AuditAppendTests(unittest.TestCase):
    def test_empty_store_has_genesis_head(self):
        connection, store = audit_store()  # noqa: F405
        verification = store.verify_chain()
        self.assertEqual(verification.event_count, 0)
        self.assertEqual(verification.last_sequence, 0)
        self.assertEqual(verification.head_hash, genesis_hash())  # noqa: F405
        connection.close()

    def test_append_normalizes_fields_and_redacts_metadata(self):
        connection, store = audit_store()  # noqa: F405
        event = append_sample(  # noqa: F405
            store,
            actor_kind="support",
            actor_id="support-a",
            effective_principal_id="customer-a",
            outcome="denied",
            source_ip="2001:4860:4860:0:0:0:0:8888",
            metadata={
                "authorization": "Bearer actual-secret",
                "nested": {"api_key": "raw-key", "safe": "visible"},
                "private": "-----BEGIN PRIVATE KEY----- secret",
            },
        )
        self.assertEqual(event.sequence, 1)
        self.assertEqual(event.source_ip, "2001:4860:4860::8888")
        self.assertEqual(event.effective_principal_id, "customer-a")
        self.assertEqual(event.metadata["authorization"], REDACTED)  # noqa: F405
        self.assertEqual(event.metadata["nested"]["api_key"], REDACTED)  # noqa: F405
        self.assertEqual(event.metadata["nested"]["safe"], "visible")
        self.assertEqual(event.metadata["private"], REDACTED)  # noqa: F405
        self.assertEqual(event.previous_hash, genesis_hash())  # noqa: F405
        self.assertEqual(store.verify_chain().head_hash, event.event_hash)
        connection.close()

    def test_timestamp_cannot_move_backwards(self):
        connection, store = audit_store()  # noqa: F405
        append_sample(store, at=NOW)  # noqa: F405
        with self.assertRaises(StateError):  # noqa: F405
            append_sample(store, at=NOW - 1)  # noqa: F405
        self.assertEqual(store.verify_chain().event_count, 1)
        connection.close()

    def test_retention_classes_compute_expected_expiry(self):
        policy = RetentionPolicy(86400, 172800, 259200)  # noqa: F405
        connection, store = audit_store(policy=policy)  # noqa: F405
        expected = {"standard": NOW + 86400, "security": NOW + 172800, "compliance": NOW + 259200, "permanent": None}  # noqa: F405
        for offset, (name, expiry) in enumerate(expected.items()):
            event = append_sample(store, retention_class=name, at=NOW + offset)  # noqa: F405
            self.assertEqual(event.expires_at, None if expiry is None else expiry + offset)
        connection.close()

    def test_append_preserves_caller_transaction_and_rolls_back(self):
        connection, store = audit_store()  # noqa: F405
        connection.commit()
        connection.execute("BEGIN")
        event = append_sample(store)  # noqa: F405
        self.assertTrue(connection.in_transaction)
        self.assertEqual(store.get_event(event.event_id, tenant_id="tenant-a").sequence, 1)
        connection.rollback()
        self.assertEqual(store.verify_chain().event_count, 0)
        connection.close()

    def test_migration_preserves_outer_transaction(self):
        connection = sqlite3.connect(":memory:")
        store = ApiAuditStore(connection)  # noqa: F405
        connection.execute("BEGIN")
        store.migrate()
        self.assertTrue(connection.in_transaction)
        connection.rollback()
        self.assertIsNone(connection.execute("SELECT name FROM sqlite_master WHERE name='hp_api_audit_events'").fetchone())
        connection.close()

    def test_events_are_append_only(self):
        connection, store = audit_store()  # noqa: F405
        append_sample(store)  # noqa: F405
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute("UPDATE hp_api_audit_events SET outcome='failed' WHERE sequence=1")
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute("DELETE FROM hp_api_audit_events WHERE sequence=1")
        connection.close()

    def test_inconsistent_chain_head_blocks_new_append(self):
        connection, store = audit_store()  # noqa: F405
        append_sample(store)  # noqa: F405
        connection.execute("UPDATE hp_api_audit_state SET head_hash = zeroblob(32) WHERE singleton=1")
        with self.assertRaises(StateError):  # noqa: F405
            append_sample(store, at=NOW + 1)  # noqa: F405
        connection.close()

    def test_duplicate_generated_event_id_is_conflict(self):
        connection, store = audit_store()  # noqa: F405
        store.new_event_id = lambda: "aud_ABCDEFGHIJKLMNOPQRSTUV"
        append_sample(store)  # noqa: F405
        with self.assertRaises(ConflictError):  # noqa: F405
            append_sample(store, at=NOW + 1)  # noqa: F405
        self.assertEqual(store.verify_chain().event_count, 1)
        connection.close()

    def test_invalid_event_fields_fail_closed(self):
        connection, store = audit_store()  # noqa: F405
        cases = (
            {"tenant_id": ""},
            {"actor_kind": "root"},
            {"actor_id": "bad\nactor"},
            {"action": "UPPER"},
            {"outcome": "allowed"},
            {"target_kind": "site", "target_id": None},
            {"request_id": "short"},
            {"source_ip": "not-an-ip"},
            {"reason_code": "BAD"},
            {"retention_class": "custom"},
        )
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(ValidationError):  # noqa: F405
                append_sample(store, **changes)  # noqa: F405
        self.assertEqual(store.verify_chain().event_count, 0)
        connection.close()


if __name__ == "__main__":
    unittest.main()
