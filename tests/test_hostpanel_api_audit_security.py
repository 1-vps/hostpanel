from __future__ import annotations

import concurrent.futures
import pathlib
import sqlite3
import tempfile
import unittest

from tests.audit_test_support import *  # noqa: F403


class AuditSecurityTests(unittest.TestCase):
    def test_chain_verifies_multiple_events(self):
        connection, store = audit_store()  # noqa: F405
        events = [append_sample(store, at=NOW + i) for i in range(5)]  # noqa: F405
        verification = store.verify_chain()
        self.assertEqual(verification.event_count, 5)
        self.assertEqual(verification.last_sequence, 5)
        self.assertEqual(verification.head_hash, events[-1].event_hash)
        connection.close()

    def test_row_tampering_is_detected_with_exact_triggers_restored(self):
        connection, store = audit_store()  # noqa: F405
        append_sample(store)  # noqa: F405
        connection.execute("DROP TRIGGER hp_api_audit_no_update")
        connection.execute("UPDATE hp_api_audit_events SET metadata_json='{}' WHERE sequence=1")
        recreate_event_triggers(connection)  # noqa: F405
        with self.assertRaises(StateError):  # noqa: F405
            store.verify_chain()
        connection.close()

    def test_tail_deletion_is_detected_by_stored_count_and_head(self):
        connection, store = audit_store()  # noqa: F405
        append_sample(store)  # noqa: F405
        append_sample(store, at=NOW + 1)  # noqa: F405
        connection.execute("DROP TRIGGER hp_api_audit_no_delete")
        connection.execute("DELETE FROM hp_api_audit_events WHERE sequence=2")
        recreate_event_triggers(connection)  # noqa: F405
        with self.assertRaises(StateError):  # noqa: F405
            store.verify_chain()
        connection.close()

    def test_schema_tampering_is_detected(self):
        connection, store = audit_store()  # noqa: F405
        connection.execute("DROP INDEX hp_api_audit_action_idx")
        connection.execute("CREATE INDEX hp_api_audit_action_idx ON hp_api_audit_events(action)")
        with self.assertRaises(StateError):  # noqa: F405
            store.verify_schema()
        with self.assertRaises(StateError):  # noqa: F405
            store.verify_chain()
        connection.close()

    def test_metadata_redaction_validation_limits(self):
        self.assertEqual(redact_metadata({"password": "secret"})[0]["password"], REDACTED)  # noqa: F405
        invalid = (
            {"float": 1.5},
            {"nul": "bad\0text"},
            {"oversize": "x" * 40000},
            {"nested": [[[[[[[[[[[[[[[[[[1]]]]]]]]]]]]]]]]]]},
        )
        for value in invalid:
            with self.subTest(value=type(value)), self.assertRaises(ValidationError):  # noqa: F405
                redact_metadata(value)  # noqa: F405

    def test_raw_secrets_are_absent_from_database_and_exports(self):
        connection, store = audit_store()  # noqa: F405
        secret = "super-secret-value"
        event = append_sample(store, metadata={"api_token": secret, "authorization": "Bearer " + secret})  # noqa: F405
        dump = " ".join(str(value) for row in connection.execute("SELECT * FROM hp_api_audit_events") for value in row)
        self.assertNotIn(secret, dump)
        self.assertNotIn(secret.encode(), store.export_jsonl((event,)))
        self.assertNotIn(secret.encode(), store.export_csv((event,)))
        connection.close()

    def test_concurrent_append_produces_one_ordered_global_chain(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(pathlib.Path(directory) / "audit.db")
            setup = sqlite3.connect(database)
            store = ApiAuditStore(setup)  # noqa: F405
            store.migrate(); setup.commit(); setup.close()

            def append(index):
                connection = sqlite3.connect(database, timeout=10)
                local = ApiAuditStore(connection)  # noqa: F405
                event = append_sample(local, tenant=f"tenant-{index % 2}", actor=f"user-{index}", at=NOW)  # noqa: F405
                connection.commit(); connection.close()
                return event.event_id

            with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
                ids = list(pool.map(append, range(20)))
            self.assertEqual(len(set(ids)), 20)
            verify_connection = sqlite3.connect(database)
            verify = ApiAuditStore(verify_connection)  # noqa: F405
            result = verify.verify_chain()
            self.assertEqual(result.event_count, 20)
            self.assertEqual([row[0] for row in verify_connection.execute("SELECT sequence FROM hp_api_audit_events ORDER BY sequence")], list(range(1, 21)))
            verify_connection.close()

    def test_package_exposes_no_delete_or_network_process_surface(self):
        source = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "tools" / "hostpanel_api_audit").glob("*.py"))  # noqa: F405
        for forbidden in ("subprocess", "socket.", "urllib", "requests", "importlib", "os.system", "DELETE FROM hp_api_audit_events", "def delete_event", "def purge"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
