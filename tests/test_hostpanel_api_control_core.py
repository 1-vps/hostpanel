from __future__ import annotations

from tests.api_control_test_support import *  # noqa: F403



class HostpanelApiControlCoreTests(ApiControlTestCase):  # noqa: F405
    def test_migration_is_idempotent_and_fail_closed(self) -> None:
        self.store.migrate()
        self.assertEqual(
            self.connection.execute("SELECT schema_version FROM hp_api_runtime_state").fetchone()[0],
            1,
        )
        self.assertEqual(self.connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertEqual(self.connection.execute("PRAGMA trusted_schema").fetchone()[0], 0)
        self.connection.execute("DROP INDEX hp_api_jobs_claim_idx")
        self.connection.execute(
            "CREATE INDEX hp_api_jobs_claim_idx ON hp_api_jobs(status)"
        )
        with self.assertRaisesRegex(ValidationError, "unexpected HostPanel API runtime schema object"):
            self.store.migrate()

    def test_idempotency_claim_completion_and_replay(self) -> None:
        first = self.store.begin_idempotent_request(
            principal_id="sa_test",
            key="deploy-request-0001",
            http_method="POST",
            request_route="/api/v1/sites",
            request_body=b'{"name":"example"}',
            expires_at=NOW + 3600,
            now=NOW,
        )
        self.assertTrue(first.created)
        pending = self.store.begin_idempotent_request(
            principal_id="sa_test",
            key="deploy-request-0001",
            http_method="POST",
            request_route="/api/v1/sites",
            request_body=b'{"name":"example"}',
            expires_at=NOW + 3600,
            now=NOW + 1,
        )
        self.assertFalse(pending.created)
        self.assertEqual(pending.state, "pending")
        completed = self.store.complete_idempotent_request(
            first.record_id,
            response_status=201,
            response_headers_map={"Content-Type": "application/json"},
            response_body=b'{"id":"site-1"}',
            now=NOW + 2,
        )
        self.assertEqual(completed.response_status, 201)
        replay = self.store.begin_idempotent_request(
            principal_id="sa_test",
            key="deploy-request-0001",
            http_method="POST",
            request_route="/api/v1/sites",
            request_body=b'{"name":"example"}',
            expires_at=NOW + 3600,
            now=NOW + 3,
        )
        self.assertEqual(replay.state, "completed")
        self.assertEqual(replay.response_headers, {"content-type": "application/json"})
        self.assertEqual(replay.response_body, b'{"id":"site-1"}')

    def test_idempotency_mismatch_expiry_and_completion_state(self) -> None:
        first = self.store.begin_idempotent_request(
            principal_id="sa_test",
            key="deploy-request-0002",
            http_method="POST",
            request_route="/api/v1/jobs",
            request_body=b"one",
            expires_at=NOW + 10,
            now=NOW,
        )
        with self.assertRaises(ConflictError):
            self.store.begin_idempotent_request(
                principal_id="sa_test",
                key="deploy-request-0002",
                http_method="POST",
                request_route="/api/v1/jobs",
                request_body=b"two",
                expires_at=NOW + 10,
                now=NOW + 1,
            )
        replacement = self.store.begin_idempotent_request(
            principal_id="sa_test",
            key="deploy-request-0002",
            http_method="POST",
            request_route="/api/v1/jobs",
            request_body=b"two",
            expires_at=NOW + 20,
            now=NOW + 10,
        )
        self.assertTrue(replacement.created)
        self.assertNotEqual(first.record_id, replacement.record_id)
        self.store.complete_idempotent_request(
            replacement.record_id,
            response_status=202,
            response_body=b"ok",
            now=NOW + 11,
        )
        with self.assertRaises(StateError):
            self.store.complete_idempotent_request(
                replacement.record_id,
                response_status=202,
                response_body=b"ok",
                now=NOW + 12,
            )

    def test_idempotency_outer_transaction_rolls_back(self) -> None:
        self.connection.execute("BEGIN")
        decision = self.store.begin_idempotent_request(
            principal_id="sa_test",
            key="rollback-request-01",
            http_method="POST",
            request_route="/api/v1/jobs",
            request_body=b"{}",
            expires_at=NOW + 60,
            now=NOW,
        )
        self.assertTrue(decision.created)
        self.connection.rollback()
        self.assertEqual(
            self.connection.execute("SELECT count(*) FROM hp_api_idempotency").fetchone()[0],
            0,
        )

    def test_job_dedupe_claim_progress_log_and_complete(self) -> None:
        first = self.job(dedupe_key="deploy-example-01")
        duplicate = self.job(dedupe_key="deploy-example-01")
        self.assertTrue(first.created)
        self.assertFalse(duplicate.created)
        self.assertEqual(first.job.job_id, duplicate.job.job_id)
        with self.assertRaises(ConflictError):
            self.job(
                dedupe_key="deploy-example-01",
                payload={"site": "other.example"},
            )
        claimed = self.store.claim_job(worker_id="worker-a", now=NOW)
        self.assertEqual(claimed.job_id, first.job.job_id)
        self.assertEqual(claimed.attempts, 1)
        heartbeat = self.store.heartbeat_job(
            claimed.job_id,
            worker_id="worker-a",
            progress=40,
            now=NOW + 1,
        )
        self.assertEqual(heartbeat.progress, 40)
        log = self.store.append_job_log(
            claimed.job_id,
            worker_id="worker-a",
            level="info",
            message="deployment started",
            now=NOW + 1,
        )
        self.assertEqual(log.sequence, 1)
        done = self.store.complete_job(
            claimed.job_id,
            worker_id="worker-a",
            result={"release": "r1"},
            now=NOW + 2,
        )
        self.assertEqual(done.status, "succeeded")
        self.assertEqual(done.progress, 100)
        self.assertEqual(done.result, {"release": "r1"})
        self.assertEqual(self.store.list_job_logs(claimed.job_id), (log,))

    def test_job_cancel_pending_and_running(self) -> None:
        pending = self.job(job_type="backup.create").job
        cancelled = self.store.request_job_cancel(pending.job_id, now=NOW + 1)
        self.assertEqual(cancelled.status, "cancelled")
        running = self.job(job_type="backup.restore").job
        claimed = self.store.claim_job(worker_id="worker-a", now=NOW + 2)
        self.assertEqual(claimed.job_id, running.job_id)
        requested = self.store.request_job_cancel(running.job_id, now=NOW + 3)
        self.assertTrue(requested.cancel_requested)
        finished = self.store.complete_job(
            running.job_id,
            worker_id="worker-a",
            result={"ignored": True},
            now=NOW + 4,
        )
        self.assertEqual(finished.status, "cancelled")
        self.assertIsNone(finished.result)

    def test_job_retry_failure_and_lease_recovery(self) -> None:
        created = self.job(max_attempts=2).job
        claimed = self.store.claim_job(
            worker_id="worker-a", lease_seconds=5, now=NOW
        )
        retry = self.store.fail_job(
            claimed.job_id,
            worker_id="worker-a",
            error="temporary",
            retry_at=NOW + 10,
            now=NOW + 1,
        )
        self.assertEqual(retry.status, "retry_wait")
        self.assertIsNone(self.store.claim_job(worker_id="worker-b", now=NOW + 9))
        second = self.store.claim_job(worker_id="worker-b", now=NOW + 10)
        self.assertEqual(second.attempts, 2)
        failed = self.store.fail_job(
            second.job_id,
            worker_id="worker-b",
            error="permanent",
            retry_at=NOW + 20,
            now=NOW + 11,
        )
        self.assertEqual(failed.status, "failed")

        lease = self.job(job_type="dns.rebuild", max_attempts=2).job
        active = self.store.claim_job(
            worker_id="worker-a", lease_seconds=5, now=NOW + 30
        )
        self.assertEqual(active.job_id, lease.job_id)
        recovered = self.store.claim_job(worker_id="worker-b", now=NOW + 36)
        self.assertEqual(recovered.job_id, lease.job_id)
        self.assertEqual(recovered.attempts, 2)
        self.assertEqual(recovered.error, "worker lease expired")

    def test_job_lease_owner_progress_and_log_limits_fail_closed(self) -> None:
        created = self.job().job
        claimed = self.store.claim_job(worker_id="worker-a", now=NOW)
        with self.assertRaises(StateError):
            self.store.heartbeat_job(
                created.job_id,
                worker_id="worker-b",
                progress=10,
                now=NOW + 1,
            )
        self.store.heartbeat_job(
            claimed.job_id,
            worker_id="worker-a",
            progress=50,
            now=NOW + 1,
        )
        with self.assertRaises(StateError):
            self.store.heartbeat_job(
                claimed.job_id,
                worker_id="worker-a",
                progress=40,
                now=NOW + 2,
            )
        with self.assertRaises(ValidationError):
            self.store.append_job_log(
                claimed.job_id,
                worker_id="worker-a",
                level="info",
                message="x" * 4097,
                now=NOW + 2,
            )

    def test_job_listing_is_stable_and_bounded(self) -> None:
        first = self.job(job_type="job.one").job
        second = self.job(job_type="job.two").job
        page = self.store.list_jobs(limit=1)
        self.assertEqual(len(page), 1)
        cursor = (page[0].created_at, page[0].job_id)
        next_page = self.store.list_jobs(after=cursor, limit=1)
        self.assertEqual(
            {page[0].job_id, next_page[0].job_id},
            {first.job_id, second.job_id},
        )
        with self.assertRaises(ValidationError):
            self.store.list_jobs(limit=1001)

    def test_outbox_and_job_respect_outer_transaction(self) -> None:
        self.connection.execute("BEGIN")
        self.job(job_type="transaction.job")
        self.outbox(event_type="transaction.event")
        self.connection.rollback()
        self.assertEqual(self.connection.execute("SELECT count(*) FROM hp_api_jobs").fetchone()[0], 0)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM hp_api_outbox").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()  # noqa: F405
