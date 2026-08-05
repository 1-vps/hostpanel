from __future__ import annotations

from tests.api_control_test_support import *  # noqa: F403



class HostpanelApiControlConcurrencyTests(ApiControlTestCase):  # noqa: F405
    def test_cross_connection_job_claim_is_single_winner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "control.db")
            first_connection = sqlite3.connect(database)
            second_connection = sqlite3.connect(database)
            first_store = ApiControlStore(first_connection)
            second_store = ApiControlStore(second_connection)
            first_store.migrate()
            first_store.create_job(
                tenant_id="tenant-a",
                job_type="site.deploy",
                payload={"site": "example.com"},
                now=NOW,
            )
            winner = first_store.claim_job(worker_id="worker-a", now=NOW)
            loser = second_store.claim_job(worker_id="worker-b", now=NOW)
            self.assertIsNotNone(winner)
            self.assertIsNone(loser)
            first_connection.close()
            second_connection.close()

    def test_cross_connection_idempotency_has_one_creator(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "control.db")
            setup = sqlite3.connect(database)
            ApiControlStore(setup).migrate()
            setup.close()
            barrier = threading.Barrier(2)
            results = []
            errors = []

            def run() -> None:
                connection = sqlite3.connect(database, timeout=5)
                store = ApiControlStore(connection)
                try:
                    barrier.wait(timeout=5)
                    results.append(
                        store.begin_idempotent_request(
                            principal_id="sa_concurrent",
                            key="concurrent-request-01",
                            http_method="POST",
                            request_route="/api/v1/jobs",
                            request_body=b"{}",
                            expires_at=NOW + 60,
                            now=NOW,
                        )
                    )
                except BaseException as exc:
                    errors.append(exc)
                finally:
                    connection.close()

            threads = [threading.Thread(target=run) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)
            self.assertFalse(any(thread.is_alive() for thread in threads))
            self.assertEqual(errors, [])
            self.assertEqual(len(results), 2)
            self.assertEqual(sum(result.created for result in results), 1)
            self.assertEqual({result.record_id for result in results}, {results[0].record_id})

    def test_cross_connection_outbox_claim_is_single_winner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "control.db")
            setup = sqlite3.connect(database)
            setup_store = ApiControlStore(setup)
            setup_store.migrate()
            event = setup_store.enqueue_outbox_event(
                destination_id="webhook-concurrent",
                event_type="site.created",
                payload={"site": "example.com"},
                now=NOW,
            ).event
            setup.close()
            barrier = threading.Barrier(2)
            results = []
            errors = []

            def run(worker: str) -> None:
                connection = sqlite3.connect(database, timeout=5)
                store = ApiControlStore(connection)
                try:
                    barrier.wait(timeout=5)
                    results.append(
                        store.claim_outbox_event(worker_id=worker, now=NOW)
                    )
                except BaseException as exc:
                    errors.append(exc)
                finally:
                    connection.close()

            threads = [
                threading.Thread(target=run, args=("sender-a",)),
                threading.Thread(target=run, args=("sender-b",)),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)
            self.assertFalse(any(thread.is_alive() for thread in threads))
            self.assertEqual(errors, [])
            winners = [result for result in results if result is not None]
            self.assertEqual(len(winners), 1)
            self.assertEqual(winners[0].event_id, event.event_id)


if __name__ == "__main__":
    unittest.main()  # noqa: F405
