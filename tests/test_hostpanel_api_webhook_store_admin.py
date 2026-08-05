from __future__ import annotations

import tempfile

from tests.webhook_test_support import *  # noqa: F403

class WebhookDestinationStoreAdminTests(unittest.TestCase):  # noqa: F405
    def test_create_load_and_event_filter_are_exact(self):
            connection, store = destination_store()  # noqa: F405
            item = store.get_destination("destination-a", tenant_id="tenant-a")
            self.assertEqual(item.host, "hooks.example.net")
            self.assertEqual(item.secret_ref, "vault:webhooks/destination-a")
            self.assertEqual(item.event_types, ("site.deleted", "site.updated"))
            self.assertTrue(item.enabled)
            self.assertEqual(item.version, 1)
            self.assertEqual(store.resolve_for_delivery("destination-a", "site.updated"), item)
            with self.assertRaises(StateError):  # noqa: F405
                store.resolve_for_delivery("destination-a", "mail.created")
            connection.close()

    def test_secret_rotation_and_filter_update_use_optimistic_version(self):
            connection, store = destination_store()  # noqa: F405
            updated = store.update_destination(
                "destination-a",
                tenant_id="tenant-a",
                expected_version=1,
                url="https://hooks.example.net/new",
                secret_reference="vault:webhooks/destination-b",
                event_types=("mail.created",),
                now=NOW,
            )
            self.assertEqual(updated.version, 2)
            self.assertEqual(updated.secret_ref, "vault:webhooks/destination-b")
            self.assertEqual(updated.event_types, ("mail.created",))
            with self.assertRaises(ConflictError):  # noqa: F405
                store.update_destination(
                    "destination-a",
                    tenant_id="tenant-a",
                    expected_version=1,
                    url="https://hooks.example.net/again",
                    secret_reference="vault:webhooks/destination-c",
                    event_types=("mail.created",),
                    now=NOW + 1,
                )
            connection.close()

    def test_disable_is_tenant_bound_and_irreversible_in_this_slice(self):
            connection, store = destination_store()  # noqa: F405
            with self.assertRaises(ConflictError):  # noqa: F405
                store.disable_destination("destination-a", tenant_id="tenant-b", expected_version=1, now=NOW)
            disabled = store.disable_destination("destination-a", tenant_id="tenant-a", expected_version=1, now=NOW)
            self.assertFalse(disabled.enabled)
            self.assertEqual(disabled.version, 2)
            with self.assertRaises(StateError):  # noqa: F405
                store.resolve_for_delivery("destination-a", "site.updated")
            connection.close()

    def test_duplicate_and_invalid_filters_fail_closed(self):
            connection, store = destination_store()  # noqa: F405
            with self.assertRaises(ConflictError):  # noqa: F405
                store.create_destination(
                    destination_id="destination-a",
                    tenant_id="tenant-a",
                    url="https://hooks.example.net/other",
                    secret_reference="vault:webhooks/other",
                    event_types=("site.updated",),
                    now=NOW,
                )
            for values in ((), "site.updated", ("BadType",)):
                with self.subTest(values=values), self.assertRaises((StateError, ValidationError)):  # noqa: F405
                    store.create_destination(
                        destination_id="destination-new",
                        tenant_id="tenant-a",
                        url="https://hooks.example.net/other",
                        secret_reference="vault:webhooks/other",
                        event_types=values,
                        now=NOW,
                    )
            connection.close()


if __name__ == "__main__":
    unittest.main()  # noqa: F405
