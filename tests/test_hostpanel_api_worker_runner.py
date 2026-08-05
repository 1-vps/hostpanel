from __future__ import annotations

from tests.worker_test_support import *  # noqa: F403


class WorkerRunnerTests(unittest.TestCase):  # noqa: F405
    def test_successful_handler_claims_heartbeats_logs_and_completes(self):
        store = FakeStore([job()])  # noqa: F405
        seen = []

        def handler(context, payload):
            seen.append((context.job_id, context.tenant_id, context.attempts, payload["site_id"]))
            context.checkpoint(40)
            context.log("info", "halfway")
            return {"site_id": payload["site_id"]}

        outcome = runner(store, {"site.rebuild": handler}).run_once()  # noqa: F405
        self.assertEqual(outcome.status, "succeeded")
        self.assertEqual(seen, [(job().job_id, "tenant-a", 1, "site-a")])  # noqa: F405
        self.assertEqual(store.jobs[job().job_id].progress, 100)  # noqa: F405
        self.assertEqual([call[0] for call in store.calls], ["claim", "heartbeat", "log", "heartbeat", "log", "heartbeat", "log", "complete"])

    def test_payload_is_recursively_read_only(self):
        store = FakeStore([job()])  # noqa: F405

        def handler(context, payload):
            with self.assertRaises(TypeError):
                payload["new"] = 1
            with self.assertRaises(TypeError):
                payload["nested"]["value"] = 2
            self.assertIsInstance(payload["nested"]["values"], tuple)
            return None

        self.assertEqual(runner(store, {"site.rebuild": handler}).run_once().status, "succeeded")  # noqa: F405

    def test_idle_claim_returns_idle_without_handler_calls(self):
        store = FakeStore()
        outcome = runner(store).run_once()  # noqa: F405
        self.assertEqual(outcome, RunOutcome("idle"))  # noqa: F405
        self.assertEqual([call[0] for call in store.calls], ["claim"])

    def test_unregistered_type_fails_closed_and_never_invokes_other_handler(self):
        store = FakeStore([job("unknown.job")])  # noqa: F405
        called = []
        outcome = runner(store, {"site.rebuild": lambda context, payload: called.append(True)}).run_once()  # noqa: F405
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(called, [])
        fail = next(call for call in store.calls if call[0] == "fail")
        self.assertEqual(fail[3], "unregistered job type")

    def test_cooperative_cancellation_is_observed_before_handler(self):
        store = FakeStore([job(cancel_requested=True)])  # noqa: F405
        called = []
        outcome = runner(store, {"site.rebuild": lambda context, payload: called.append(True)}).run_once()  # noqa: F405
        self.assertEqual(outcome.status, "cancelled")
        self.assertEqual(called, [])

    def test_cancellation_observed_at_handler_checkpoint(self):
        store = FakeStore([job()])  # noqa: F405

        def handler(context, payload):
            store.cancel_on_heartbeat = True
            context.checkpoint(10)
            self.fail("checkpoint should raise cancellation")

        outcome = runner(store, {"site.rebuild": handler}).run_once()  # noqa: F405
        self.assertEqual(outcome.status, "cancelled")

    def test_progress_cannot_move_backwards_or_outside_bounds(self):
        store = FakeStore([job(progress=20)])  # noqa: F405

        def handler(context, payload):
            for value in (-1, 19, 101, True):
                with self.assertRaises(ValueError):
                    context.checkpoint(value)
            return None

        self.assertEqual(runner(store, {"site.rebuild": handler}).run_once().status, "succeeded")  # noqa: F405

    def test_invalid_handler_result_fails_with_public_contract_error(self):
        store = FakeStore([job()])  # noqa: F405
        outcome = runner(store, {"site.rebuild": lambda context, payload: ["bad"]}).run_once()  # noqa: F405
        self.assertEqual(outcome.status, "failed")
        fail = next(call for call in store.calls if call[0] == "fail")
        self.assertEqual(fail[3], "invalid handler result")


if __name__ == "__main__":
    unittest.main()  # noqa: F405
