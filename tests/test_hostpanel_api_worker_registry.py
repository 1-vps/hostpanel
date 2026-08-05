from __future__ import annotations

from tests.worker_test_support import *  # noqa: F403


class WorkerRegistryTests(unittest.TestCase):  # noqa: F405
    def test_registry_is_fixed_sorted_and_resolves_exact_types(self):
        first = lambda context, payload: None
        second = lambda context, payload: {"ok": True}
        registry = HandlerRegistry([("z.cleanup", second), ("a.provision", first)])  # noqa: F405
        self.assertEqual(registry.job_types, ("a.provision", "z.cleanup"))
        self.assertIs(registry.resolve("a.provision"), first)
        self.assertIsNone(registry.resolve("missing.job"))
        self.assertEqual(len(registry), 2)

    def test_duplicate_empty_invalid_and_noncallable_entries_are_rejected(self):
        cases = (
            [],
            [("a.job", lambda c, p: None), ("a.job", lambda c, p: None)],
            [("BadJob", lambda c, p: None)],
            [("a.job", "module:function")],
        )
        for value in cases:
            with self.subTest(value=value), self.assertRaises(RegistryError):  # noqa: F405
                HandlerRegistry(value)  # noqa: F405

    def test_registry_copies_input_and_exposes_no_mutation_api(self):
        handlers = {"a.job": lambda context, payload: None}
        registry = HandlerRegistry(handlers)  # noqa: F405
        handlers["b.job"] = lambda context, payload: None
        self.assertEqual(registry.job_types, ("a.job",))
        self.assertFalse(hasattr(registry, "register"))
        self.assertFalse(hasattr(registry, "load"))

    def test_worker_config_enforces_reviewed_bounds(self):
        WorkerConfig("worker-a")  # noqa: F405
        invalid = (
            {"worker_id": " bad"},
            {"worker_id": "worker-a", "lease_seconds": 0},
            {"worker_id": "worker-a", "idle_sleep_seconds": 0},
            {"worker_id": "worker-a", "max_jobs_per_cycle": 0},
            {"worker_id": "worker-a", "retry_base_seconds": 10, "retry_max_seconds": 5},
        )
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(ValueError):
                WorkerConfig(**values)  # noqa: F405

    def test_retryable_error_rejects_invalid_delay_and_sanitizes_message(self):
        with self.assertRaises(HandlerContractError):  # noqa: F405
            RetryableJobError("temporary", retry_after=0)  # noqa: F405
        error = RetryableJobError("secret\nline", retry_after=5)  # noqa: F405
        self.assertEqual(error.public_message, "secret line")
        self.assertEqual(error.retry_after, 5)


if __name__ == "__main__":
    unittest.main()  # noqa: F405
