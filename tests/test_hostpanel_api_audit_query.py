from __future__ import annotations

import unittest

from tests.audit_test_support import *  # noqa: F403


class AuditQueryTests(unittest.TestCase):
    def setUp(self):
        self.connection, self.store = audit_store()  # noqa: F405
        self.events = (
            append_sample(self.store, tenant="tenant-a", actor="user-a", action="site.created", outcome="succeeded", target_kind="site", target_id="site-a", at=NOW),  # noqa: F405
            append_sample(self.store, tenant="tenant-a", actor="support-a", actor_kind="support", effective_principal_id="user-a", action="site.updated", outcome="denied", target_kind="site", target_id="site-a", at=NOW),  # noqa: F405
            append_sample(self.store, tenant="tenant-b", actor="user-b", action="mail.created", outcome="failed", target_kind="mailbox", target_id="mail-a", at=NOW + 1),  # noqa: F405
        )

    def tearDown(self):
        self.connection.close()

    def test_search_is_tenant_bound(self):
        self.assertEqual([e.event_id for e in self.store.search_events(tenant_id="tenant-a")], [self.events[0].event_id, self.events[1].event_id])
        self.assertEqual([e.event_id for e in self.store.search_events(tenant_id="tenant-b")], [self.events[2].event_id])

    def test_get_event_does_not_cross_tenants(self):
        self.assertEqual(self.store.get_event(self.events[0].event_id, tenant_id="tenant-a"), self.events[0])
        with self.assertRaises(StateError):  # noqa: F405
            self.store.get_event(self.events[0].event_id, tenant_id="tenant-b")
        with self.assertRaises(ValidationError):  # noqa: F405
            self.store.get_event("not-an-audit-id", tenant_id="tenant-a")

    def test_exact_filters(self):
        cases = (
            ({"actor_kind": "support"}, self.events[1]),
            ({"actor_id": "support-a"}, self.events[1]),
            ({"effective_principal_id": "user-a"}, self.events[1]),
            ({"action": "site.created"}, self.events[0]),
            ({"outcome": "denied"}, self.events[1]),
            ({"request_id": "request-0001"}, self.events[0]),
            ({"reason_code": "user_request"}, self.events[0]),
            ({"retention_class": "standard"}, self.events[0]),
            ({"target_kind": "site", "target_id": "site-a"}, self.events[0]),
            ({"from_at": NOW + 1, "to_at": NOW + 1}, None),
        )
        for filters, expected in cases:
            with self.subTest(filters=filters):
                found = self.store.search_events(tenant_id="tenant-a", **filters)
                if expected is None:
                    self.assertEqual(found, ())
                elif filters.get("target_kind") or filters.get("request_id") or filters.get("reason_code") or filters.get("retention_class"):
                    self.assertEqual(found, self.events[:2])
                else:
                    self.assertEqual(found, (expected,))

    def test_cursor_is_stable_for_equal_timestamps(self):
        first = self.store.search_events(tenant_id="tenant-a", limit=1)
        second = self.store.search_events(tenant_id="tenant-a", after=(first[0].occurred_at, first[0].sequence), limit=10)
        self.assertEqual(first, (self.events[0],))
        self.assertEqual(second, (self.events[1],))

    def test_invalid_ranges_cursors_and_limits_fail(self):
        cases = (
            {"from_at": NOW + 1, "to_at": NOW},
            {"after": (NOW,)},
            {"after": (NOW, -1)},
            {"limit": 0},
            {"limit": 1001},
            {"target_kind": "site"},
            {"actor_kind": "root"},
            {"request_id": "short"},
            {"reason_code": "BAD"},
            {"retention_class": "custom"},
        )
        for values in cases:
            with self.subTest(values=values), self.assertRaises(ValidationError):  # noqa: F405
                self.store.search_events(tenant_id="tenant-a", **values)


if __name__ == "__main__":
    unittest.main()
