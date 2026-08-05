from __future__ import annotations

import hashlib
import json
import unittest

from tests.audit_test_support import *  # noqa: F403


class AuditRetentionTests(unittest.TestCase):
    def test_policy_must_be_ordered_and_bounded(self):
        invalid = (
            RetentionPolicy(0, 100, 200),  # noqa: F405
            RetentionPolicy(200, 100, 300),  # noqa: F405
            RetentionPolicy(100, 300, 200),  # noqa: F405
        )
        for policy in invalid:
            with self.subTest(policy=policy), self.assertRaises(ValidationError):  # noqa: F405
                ApiAuditStore(__import__("sqlite3").connect(":memory:"), policy=policy)  # noqa: F405

    def test_candidates_are_expired_ordered_filterable_and_exclude_permanent(self):
        policy = RetentionPolicy(86400, 172800, 259200)  # noqa: F405
        connection, store = audit_store(policy=policy)  # noqa: F405
        first = append_sample(store, tenant="tenant-a", retention_class="standard", at=NOW)  # noqa: F405
        second = append_sample(store, tenant="tenant-b", retention_class="security", at=NOW + 1)  # noqa: F405
        append_sample(store, tenant="tenant-a", retention_class="permanent", at=NOW + 2)  # noqa: F405
        cutoff = NOW + 172801
        all_candidates = store.retention_candidates(before=cutoff)
        self.assertEqual([item.event_id for item in all_candidates], [first.event_id, second.event_id])
        self.assertEqual([item.event_id for item in store.retention_candidates(before=cutoff, tenant_id="tenant-a")], [first.event_id])
        self.assertEqual([item.event_id for item in store.retention_candidates(before=cutoff, after_sequence=first.sequence)], [second.event_id])
        connection.close()

    def test_candidate_bounds_fail_closed(self):
        connection, store = audit_store()  # noqa: F405
        for values in ({"before": -1}, {"before": NOW, "limit": 0}, {"before": NOW, "limit": 1001}, {"before": NOW, "after_sequence": -1}):
            with self.subTest(values=values), self.assertRaises(ValidationError):  # noqa: F405
                store.retention_candidates(**values)
        connection.close()

    def test_manifest_is_deterministic_bound_and_digestible(self):
        connection, store = audit_store(policy=RetentionPolicy(86400, 172800, 259200))  # noqa: F405
        append_sample(store, at=NOW)  # noqa: F405
        append_sample(store, at=NOW + 1)  # noqa: F405
        candidates = store.retention_candidates(before=NOW + 100000)
        first = store.retention_manifest(candidates)
        second = store.retention_manifest(candidates)
        self.assertEqual(first, second)
        document = json.loads(first)
        self.assertEqual(document["format"], "hostpanel-audit-retention-v1")
        self.assertEqual(document["count"], 2)
        self.assertEqual(document["first_sequence"], 1)
        self.assertEqual(document["last_sequence"], 2)
        self.assertEqual(store.retention_manifest_digest(first), hashlib.sha256(first).hexdigest())
        connection.close()

    def test_manifest_rejects_invalid_or_unordered_candidates(self):
        connection, store = audit_store()  # noqa: F405
        event = append_sample(store)  # noqa: F405
        item = RetentionCandidate(event.sequence, event.event_id, event.tenant_id, event.occurred_at, event.expires_at, event.event_hash)  # noqa: F405
        with self.assertRaises(ValidationError):  # noqa: F405
            store.retention_manifest((object(),))
        with self.assertRaises(ValidationError):  # noqa: F405
            store.retention_manifest((item, item))
        with self.assertRaises(ValidationError):  # noqa: F405
            store.retention_manifest_digest("not-bytes")
        connection.close()

    def test_retention_only_identifies_candidates_and_never_deletes(self):
        connection, store = audit_store(policy=RetentionPolicy(86400, 172800, 259200))  # noqa: F405
        event = append_sample(store, at=NOW)  # noqa: F405
        self.assertEqual(len(store.retention_candidates(before=NOW + 86400)), 1)
        self.assertEqual(store.get_event(event.event_id, tenant_id="tenant-a"), event)
        self.assertEqual(store.verify_chain().event_count, 1)
        connection.close()


if __name__ == "__main__":
    unittest.main()
