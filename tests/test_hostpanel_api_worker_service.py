from __future__ import annotations

from tests.worker_test_support import *  # noqa: F403


class Stop:
    def __init__(self, values):
        self.values = iter(values)

    def is_set(self):
        return next(self.values)


class WorkerServiceTests(unittest.TestCase):  # noqa: F405
    def test_cycle_stops_on_idle_and_preserves_outcomes(self):
        store = FakeStore([job()])  # noqa: F405
        service = WorkerService(runner(store), sleep=lambda seconds: None)  # noqa: F405
        outcomes = service.run_cycle(max_jobs=5)
        self.assertEqual([outcome.status for outcome in outcomes], ["succeeded", "idle"])

    def test_cycle_is_bounded_even_when_queue_never_idles(self):
        jobs = [
            job(job_id=f"job_{index:022d}")  # noqa: F405
            for index in range(5)
        ]
        store = FakeStore(jobs)
        service = WorkerService(runner(store), sleep=lambda seconds: None)  # noqa: F405
        outcomes = service.run_cycle(max_jobs=3)
        self.assertEqual(len(outcomes), 3)
        self.assertEqual(len(store.queue), 2)

    def test_invalid_cycle_bound_is_rejected(self):
        service = WorkerService(runner(FakeStore()), sleep=lambda seconds: None)  # noqa: F405
        for value in (0, -1, True, 10_001):
            with self.subTest(value=value), self.assertRaises(ValueError):
                service.run_cycle(max_jobs=value)

    def test_idle_loop_sleeps_and_rechecks_stop_signal(self):
        sleeps = []
        service = WorkerService(runner(FakeStore()), sleep=sleeps.append)  # noqa: F405
        service.run_until_stopped(Stop([False, False, True]))
        self.assertEqual(sleeps, [1.0])

    def test_service_requires_injected_sleep_and_starts_no_process(self):
        with self.assertRaises(ValueError):
            WorkerService(runner(FakeStore()), sleep=None)  # type: ignore[arg-type]  # noqa: F405
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ROOT / "tools" / "hostpanel_api_worker").glob("*.py")  # noqa: F405
        )
        for forbidden in ("subprocess", "os.system", "importlib", "eval(", "exec(", "serve_forever", "systemctl"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()  # noqa: F405
