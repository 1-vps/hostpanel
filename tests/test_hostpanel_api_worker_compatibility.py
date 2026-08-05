from __future__ import annotations

from tests.worker_test_support import *  # noqa: F403
from hostpanel_api_worker import REQUIRED_STORE_METHODS, verify_store_contract


class WorkerCompatibilityTests(unittest.TestCase):  # noqa: F405
    def test_reviewed_store_contract_matches_control_plane_job_api(self):
        self.assertEqual(
            REQUIRED_STORE_METHODS,
            (
                "append_job_log",
                "claim_job",
                "complete_job",
                "fail_job",
                "heartbeat_job",
            ),
        )
        verify_store_contract(FakeStore())  # noqa: F405

    def test_missing_or_noncallable_store_method_fails_at_runner_construction(self):
        class Missing:
            claim_job = None

        with self.assertRaisesRegex(RuntimeError, "reviewed worker contract"):
            runner(Missing())  # noqa: F405

    def test_log_contract_rejects_controls_oversize_and_unknown_levels_before_store_call(self):
        store = FakeStore([job()])  # noqa: F405
        errors = []

        def handler(context, payload):
            for level, message in (
                ("notice", "message"),
                ("info", "line\nbreak"),
                ("info", "x" * 4097),
                ("info", ""),
            ):
                try:
                    context.log(level, message)
                except ValueError as exc:
                    errors.append(str(exc))
            return None

        outcome = runner(store, {"site.rebuild": handler}).run_once()  # noqa: F405
        self.assertEqual(outcome.status, "succeeded")
        self.assertEqual(len(errors), 4)
        self.assertFalse(any(log[2] in {"message", "line\nbreak", "x" * 4097, ""} for log in store.logs))

    def test_payload_with_non_text_key_fails_closed(self):
        store = FakeStore([job(payload={1: "bad"})])  # noqa: F405
        reported = []
        outcome = runner(store, reporter=lambda durable_job, error: reported.append(type(error).__name__)).run_once()  # noqa: F405
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(reported, ["ValueError"])
        fail = next(call for call in store.calls if call[0] == "fail")
        self.assertEqual(fail[3], "job handler failed")


if __name__ == "__main__":
    unittest.main()  # noqa: F405
