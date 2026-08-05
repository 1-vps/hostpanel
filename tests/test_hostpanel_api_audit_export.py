from __future__ import annotations

import csv
import hashlib
import io
import json
import unittest

from tests.audit_test_support import *  # noqa: F403


class AuditExportTests(unittest.TestCase):
    def setUp(self):
        self.connection, self.store = audit_store()  # noqa: F405
        self.event = append_sample(self.store, metadata={"token": "secret", "safe": "visible"})  # noqa: F405

    def tearDown(self):
        self.connection.close()

    def test_jsonl_is_deterministic_canonical_and_redacted(self):
        first = self.store.export_jsonl((self.event,))
        second = self.store.export_jsonl((self.event,))
        self.assertEqual(first, second)
        self.assertTrue(first.endswith(b"\n"))
        record = json.loads(first)
        self.assertEqual(record["metadata"], {"safe": "visible", "token": REDACTED})  # noqa: F405
        self.assertNotIn(b"secret", first)
        self.assertEqual(record["event_hash"], self.event.event_hash.hex())

    def test_csv_is_deterministic_and_has_stable_empty_header(self):
        payload = self.store.export_csv((self.event,))
        self.assertEqual(payload, self.store.export_csv((self.event,)))
        row = next(csv.DictReader(io.StringIO(payload.decode())))
        self.assertEqual(row["event_id"], self.event.event_id)
        self.assertEqual(json.loads(row["metadata"])["token"], REDACTED)  # noqa: F405
        empty = self.store.export_csv(())
        self.assertTrue(empty.startswith(b"sequence,event_id,tenant_id,"))
        self.assertEqual(len(empty.splitlines()), 1)

    def test_csv_formula_values_are_prefixed(self):
        forged = replace_event(self.event, actor_id="=cmd|' /C calc'!A0")  # noqa: F405
        row = next(csv.DictReader(io.StringIO(self.store.export_csv((forged,)).decode())))
        self.assertTrue(row["actor_id"].startswith("'="))

    def test_export_digest_is_sha256(self):
        payload = self.store.export_jsonl((self.event,))
        self.assertEqual(self.store.export_digest(payload), hashlib.sha256(payload).hexdigest())
        with self.assertRaises(ValidationError):  # noqa: F405
            self.store.export_digest("not-bytes")

    def test_export_rejects_wrong_types_and_oversized_sets(self):
        with self.assertRaises(ValidationError):  # noqa: F405
            self.store.export_jsonl((object(),))
        with self.assertRaises(ValidationError):  # noqa: F405
            self.store.export_csv((self.event,) * 10001)


if __name__ == "__main__":
    unittest.main()
