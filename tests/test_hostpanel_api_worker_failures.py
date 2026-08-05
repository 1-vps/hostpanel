from __future__ import annotations

from tests.worker_test_support import *  # noqa: F403


class WorkerFailureTests(unittest.TestCase):  # noqa: F405
    def test_retryable_failure_uses_deterministic_bounded_backoff(self):
        clock = Clock()  # noqa: F405
        store = FakeStore([job(attempts=1)])  # noqa: F405
        outcome = runner(
            store,
            {"site.rebuild": lambda context, payload: (_ for _ in ()).throw(RetryableJobError("temporary"))},  # noqa: F405
            clock=clock,
            config=WorkerConfig("worker-a", retry_base_seconds=5, retry_max_seconds=12),  # noqa: F405
        ).run_once()
        self.assertEqual(outcome.status, "retry_wait")
        fail = next(call for call in store.calls if call[0] == "fail")
        self.assertEqual(fail[3], "temporary")
        self.assertEqual(fail[4] - fail[5], 10)

    def test_explicit_retry_delay_is_bounded_by_configuration(self):
        store = FakeStore([job()])  # noqa: F405
        outcome = runner(
            store,
            {"site.rebuild": lambda context, payload: (_ for _ in ()).throw(RetryableJobError("temporary", retry_after=8))},  # noqa: F405
            config=WorkerConfig("worker-a", retry_max_seconds=10),  # noqa: F405
        ).run_once()
        self.assertEqual(outcome.status, "retry_wait")
        store = FakeStore([job()])  # noqa: F405
        outcome = runner(
            store,
            {"site.rebuild": lambda context, payload: (_ for _ in ()).throw(RetryableJobError("temporary", retry_after=11))},  # noqa: F405
            config=WorkerConfig("worker-a", retry_max_seconds=10),  # noqa: F405
        ).run_once()
        self.assertEqual(outcome.status, "failed")

    def test_retry_at_attempt_limit_becomes_failed_via_store_contract(self):
        store = FakeStore([job(attempts=2, max_attempts=3)])  # noqa: F405
        outcome = runner(
            store,
            {"site.rebuild": lambda context, payload: (_ for _ in ()).throw(RetryableJobError())},  # noqa: F405
        ).run_once()
        self.assertEqual(outcome.status, "failed")

    def test_unexpected_exception_is_redacted_and_reported_out_of_band(self):
        store = FakeStore([job()])  # noqa: F405
        reported = []

        def handler(context, payload):
            raise RuntimeError("database password secret")

        outcome = runner(store, {"site.rebuild": handler}, reporter=lambda durable_job, error: reported.append((durable_job.job_id, str(error)))).run_once()  # noqa: F405
        self.assertEqual(outcome.status, "failed")
        fail = next(call for call in store.calls if call[0] == "fail")
        self.assertEqual(fail[3], "job handler failed")
        self.assertNotIn("password", " ".join(log[2] for log in store.logs))
        self.assertIn("password", reported[0][1])

    def test_reporter_failure_does_not_replace_durable_failure(self):
        store = FakeStore([job()])  # noqa: F405

        def reporter(durable_job, error):
            raise RuntimeError("reporter unavailable")

        outcome = runner(
            store,
            {"site.rebuild": lambda context, payload: (_ for _ in ()).throw(ValueError("bad"))},
            reporter=reporter,
        ).run_once()
        self.assertEqual(outcome.status, "failed")

    def test_lease_loss_during_heartbeat_stops_without_fail_or_complete(self):
        store = FakeStore([job()])  # noqa: F405
        store.fail_operation = "heartbeat"
        outcome = runner(store).run_once()  # noqa: F405
        self.assertEqual(outcome.status, "lease_lost")
        self.assertNotIn("fail", [call[0] for call in store.calls])
        self.assertNotIn("complete", [call[0] for call in store.calls])

    def test_lease_loss_during_log_or_complete_never_double_transitions(self):
        for operation in ("log", "complete", "fail"):
            with self.subTest(operation=operation):
                store = FakeStore([job()])  # noqa: F405
                store.fail_operation = operation
                if operation == "fail":
                    handler = lambda context, payload: (_ for _ in ()).throw(ValueError("bad"))
                else:
                    handler = lambda context, payload: {"ok": True}
                outcome = runner(store, {"site.rebuild": handler}).run_once()  # noqa: F405
                self.assertEqual(outcome.status, "lease_lost")
                self.assertLessEqual(sum(call[0] == "complete" for call in store.calls), 1)
                self.assertLessEqual(sum(call[0] == "fail" for call in store.calls), 1)

    def test_claim_storage_error_fails_closed_as_lease_lost(self):
        store = FakeStore()
        store.claim_error = True
        self.assertEqual(runner(store).run_once().status, "lease_lost")  # noqa: F405


if __name__ == "__main__":
    unittest.main()  # noqa: F405
