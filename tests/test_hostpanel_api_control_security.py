from __future__ import annotations

from tests.api_control_test_support import *  # noqa: F403



class HostpanelApiControlSecurityTests(ApiControlTestCase):  # noqa: F405
    def test_schema_rejects_impossible_runtime_states(self) -> None:
        job = self.job(job_type="schema.job").job
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                "UPDATE hp_api_jobs SET status = 'succeeded' WHERE job_id = ?",
                (job.job_id,),
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                "UPDATE hp_api_jobs SET attempts = max_attempts + 1 WHERE job_id = ?",
                (job.job_id,),
            )
        event = self.outbox(event_type="schema.event").event
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                "UPDATE hp_api_outbox SET status = 'delivered' WHERE event_id = ?",
                (event.event_id,),
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                """
                INSERT INTO hp_api_idempotency(
                    record_id, principal_id, idempotency_key, request_hash,
                    http_method, route, state, created_at, expires_at,
                    completed_at, response_status, response_headers_json, response_body
                ) VALUES(
                    'idem_abcdefghijklmnopqrstuv', 'sa_test', 'schema-status-01',
                    zeroblob(32), 'POST', '/api/v1/jobs', 'completed', ?, ?, ?, 99, '{}', x''
                )
                """,
                (NOW, NOW + 60, NOW + 1),
            )

    def test_success_after_retry_clears_stale_error(self) -> None:
        job = self.job(job_type="retry.success", max_attempts=2).job
        claimed = self.store.claim_job(worker_id="worker-a", now=NOW)
        self.assertEqual(claimed.job_id, job.job_id)
        waiting = self.store.fail_job(
            job.job_id,
            worker_id="worker-a",
            error="temporary failure",
            retry_at=NOW + 10,
            now=NOW + 1,
        )
        self.assertEqual(waiting.error, "temporary failure")
        retried = self.store.claim_job(worker_id="worker-b", now=NOW + 10)
        completed = self.store.complete_job(
            retried.job_id,
            worker_id="worker-b",
            result={"ok": True},
            now=NOW + 11,
        )
        self.assertEqual(completed.status, "succeeded")
        self.assertIsNone(completed.error)

    def test_noncanonical_persisted_json_is_rejected(self) -> None:
        job = self.job(payload={"a": 1, "b": 2}).job
        self.connection.execute(
            "UPDATE hp_api_jobs SET payload_json = ? WHERE job_id = ?",
            ('{"b":2, "a":1}', job.job_id),
        )
        with self.assertRaisesRegex(ValidationError, "not canonical"):
            self.store.list_jobs()

    def test_time_invariants_fail_closed(self) -> None:
        decision = self.store.begin_idempotent_request(
            principal_id="sa_test",
            key="time-invariant-0001",
            http_method="POST",
            request_route="/api/v1/jobs",
            request_body=b"{}",
            expires_at=NOW + 60,
            now=NOW,
        )
        with self.assertRaises(StateError):
            self.store.complete_idempotent_request(
                decision.record_id,
                response_status=202,
                response_body=b"ok",
                now=NOW - 1,
            )
        with self.assertRaises(ValidationError):
            self.job(available_at=NOW - 1)
        with self.assertRaises(ValidationError):
            self.outbox(available_at=NOW - 1)

        pending = self.job(job_type="time.pending").job
        with self.assertRaises(StateError):
            self.store.request_job_cancel(pending.job_id, now=NOW - 1)
        claimed = self.store.claim_job(worker_id="time-worker", now=NOW)
        with self.assertRaises(StateError):
            self.store.heartbeat_job(
                claimed.job_id,
                worker_id="time-worker",
                progress=1,
                now=NOW - 1,
            )
        with self.assertRaises(StateError):
            self.store.complete_job(
                claimed.job_id,
                worker_id="time-worker",
                now=NOW - 1,
            )

        event = self.outbox(event_type="time.event").event
        delivering = self.store.claim_outbox_event(worker_id="time-sender", now=NOW)
        self.assertEqual(delivering.event_id, event.event_id)
        with self.assertRaises(StateError):
            self.store.mark_outbox_delivered(
                event.event_id,
                worker_id="time-sender",
                now=NOW - 1,
            )
        with self.assertRaises(StateError):
            self.store.fail_outbox_event(
                event.event_id,
                worker_id="time-sender",
                error="clock moved backwards",
                now=NOW - 1,
            )

    def test_persisted_headers_and_json_shape_fail_closed(self) -> None:
        decision = self.store.begin_idempotent_request(
            principal_id="sa_test",
            key="persisted-headers-01",
            http_method="POST",
            request_route="/api/v1/jobs",
            request_body=b"{}",
            expires_at=NOW + 60,
            now=NOW,
        )
        self.store.complete_idempotent_request(
            decision.record_id,
            response_status=202,
            response_headers_map={"content-type": "application/json"},
            response_body=b"{}",
            now=NOW + 1,
        )
        self.connection.execute(
            "UPDATE hp_api_idempotency SET response_headers_json = '[]' WHERE record_id = ?",
            (decision.record_id,),
        )
        with self.assertRaisesRegex(ValidationError, "persisted response headers"):
            self.store.begin_idempotent_request(
                principal_id="sa_test",
                key="persisted-headers-01",
                http_method="POST",
                request_route="/api/v1/jobs",
                request_body=b"{}",
                expires_at=NOW + 60,
                now=NOW + 2,
            )

        with self.assertRaisesRegex(ValidationError, "invalid object key"):
            self.job(payload={1: "ambiguous"})
        with self.assertRaisesRegex(ValidationError, "out-of-range integer"):
            self.job(payload={"number": 2**80})
        deeply_nested: dict[str, object] = {}
        cursor = deeply_nested
        for _ in range(34):
            child: dict[str, object] = {}
            cursor["child"] = child
            cursor = child
        with self.assertRaisesRegex(ValidationError, "too deeply nested"):
            self.job(payload=deeply_nested)

    def test_input_bounds_and_ambiguous_json_fail_closed(self) -> None:
        with self.assertRaises(ValidationError):
            self.store.begin_idempotent_request(
                principal_id="sa_test",
                key="short",
                http_method="POST",
                request_route="/api/v1/jobs",
                request_body=b"{}",
                expires_at=NOW + 60,
                now=NOW,
            )
        with self.assertRaises(ValidationError):
            self.job(payload={"not-finite": float("nan")})
        with self.assertRaises(ValidationError):
            self.outbox(payload={"large": "x" * (64 << 10)})
        with self.assertRaises(ValidationError):
            sign_webhook(
                b"short",
                event_id="evt_abcdefghijklmnopqrstuv",
                payload=b"{}",
                timestamp=NOW,
            )


if __name__ == "__main__":
    unittest.main()  # noqa: F405
