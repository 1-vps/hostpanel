from __future__ import annotations

import tempfile

from tests.webhook_test_support import *  # noqa: F403

class WebhookDestinationStoreConsistencyTests(unittest.TestCase):  # noqa: F405
    def test_schema_is_exact_idempotent_and_preserves_outer_transaction(self):
            connection = sqlite3.connect(":memory:")  # noqa: F405
            store = WebhookDestinationStore(connection)  # noqa: F405
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA trusted_schema = OFF")
            connection.execute("BEGIN")
            store.migrate()
            self.assertTrue(connection.in_transaction)
            store.migrate()
            self.assertTrue(connection.in_transaction)
            connection.rollback()
            self.assertIsNone(connection.execute("SELECT name FROM sqlite_master WHERE name='hp_api_webhook_destinations'").fetchone())
            connection.close()

    def test_concurrent_expected_version_has_one_winner(self):
            with tempfile.TemporaryDirectory() as directory:
                database = str(pathlib.Path(directory) / "destinations.db")  # noqa: F405
                setup = sqlite3.connect(database)  # noqa: F405
                store = WebhookDestinationStore(setup)  # noqa: F405
                store.migrate()
                store.create_destination(
                    destination_id="destination-a", tenant_id="tenant-a",
                    url="https://hooks.example.net/a", secret_reference="vault:webhooks/a",
                    event_types=("site.updated",), now=NOW,
                )
                setup.commit(); setup.close()
                first = sqlite3.connect(database, timeout=5)  # noqa: F405
                second = sqlite3.connect(database, timeout=5)  # noqa: F405
                one = WebhookDestinationStore(first)  # noqa: F405
                two = WebhookDestinationStore(second)  # noqa: F405
                one.update_destination("destination-a", tenant_id="tenant-a", expected_version=1, url="https://hooks.example.net/b", secret_reference="vault:webhooks/b", event_types=("site.updated",), now=NOW+1)
                first.commit()
                with self.assertRaises(ConflictError):  # noqa: F405
                    two.update_destination("destination-a", tenant_id="tenant-a", expected_version=1, url="https://hooks.example.net/c", secret_reference="vault:webhooks/c", event_types=("site.updated",), now=NOW+2)
                first.close(); second.close()


if __name__ == "__main__":
    unittest.main()  # noqa: F405
