from __future__ import annotations

import threading

from tests.http_test_support import *  # noqa: F403


class HostpanelApiHttpRateLimitTests(unittest.TestCase):  # noqa: F405
    def limiter(self):
        connection = sqlite3.connect(":memory:")  # noqa: F405
        limiter = SqliteRateLimiter(connection, key_secret=b"r" * 32)  # noqa: F405
        limiter.migrate()
        return connection, limiter

    def test_fixed_window_limit_and_reset_are_exact(self) -> None:
        connection, limiter = self.limiter()
        policy = RateLimitPolicy(2, 60)  # noqa: F405
        first = limiter.consume(identity="source:192.0.2.1", operation_id="getSite", policy=policy, now=NOW)
        second = limiter.consume(identity="source:192.0.2.1", operation_id="getSite", policy=policy, now=NOW + 1)
        denied = limiter.consume(identity="source:192.0.2.1", operation_id="getSite", policy=policy, now=NOW + 2)
        reset = limiter.consume(identity="source:192.0.2.1", operation_id="getSite", policy=policy, now=NOW + 60)
        self.assertTrue(first.allowed)
        self.assertEqual(first.remaining, 1)
        self.assertTrue(second.allowed)
        self.assertEqual(second.remaining, 0)
        self.assertFalse(denied.allowed)
        self.assertEqual(denied.retry_after, 58)
        self.assertTrue(reset.allowed)
        self.assertEqual(reset.remaining, 1)
        connection.close()

    def test_source_rate_limit_returns_429_and_standard_headers(self) -> None:
        connection, limiter = self.limiter()
        app = ApiApplication(rate_limiter=limiter)  # noqa: F405
        app.register(
            Route(  # noqa: F405
                method="GET",
                path="/api/v1/public",
                operation_id="getPublic",
                summary="Read public value",
                auth_required=False,
                source_rate_limit=RateLimitPolicy(1, 60),  # noqa: F405
                handler=lambda context: {"ok": True},
            )
        )
        first = app.dispatch(Request("GET", "/api/v1/public", source_ip="192.0.2.8"), now=NOW)  # noqa: F405
        second = app.dispatch(Request("GET", "/api/v1/public", source_ip="192.0.2.8"), now=NOW + 1)  # noqa: F405
        self.assertEqual(first.status, 200)
        self.assertEqual(headers(first)["ratelimit-remaining"], "0")  # noqa: F405
        self.assertEqual(second.status, 429)
        self.assertEqual(body(second)["error"]["code"], "rate_limited")  # noqa: F405
        self.assertEqual(headers(second)["retry-after"], "59")  # noqa: F405
        connection.close()

    def test_principal_limit_is_shared_across_source_addresses(self) -> None:
        connection, limiter = self.limiter()
        app, _ = protected_app(  # noqa: F405
            rate_limiter=limiter,
            route_options={"principal_rate_limit": RateLimitPolicy(1, 60)},  # noqa: F405
        )
        first = app.dispatch(
            Request(
                "GET",
                "/api/v1/sites/example.com",
                headers=(("Authorization", f"Bearer {TOKEN}"),),  # noqa: F405
                source_ip="192.0.2.1",
            ),
            now=NOW,
        )
        second = app.dispatch(
            Request(
                "GET",
                "/api/v1/sites/example.com",
                headers=(("Authorization", f"Bearer {TOKEN}"),),  # noqa: F405
                source_ip="198.51.100.1",
            ),
            now=NOW + 1,
        )
        self.assertEqual(first.status, 200)
        self.assertEqual(second.status, 429)
        connection.close()

    def test_concurrent_connections_have_one_winner_for_last_slot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:  # noqa: F405
            database = str(Path(directory) / "rate.db")  # noqa: F405
            setup = sqlite3.connect(database)  # noqa: F405
            SqliteRateLimiter(setup, key_secret=b"r" * 32).migrate()  # noqa: F405
            setup.commit()
            setup.close()
            barrier = threading.Barrier(2)
            decisions = []
            errors = []

            def run():
                connection = sqlite3.connect(database, timeout=5)
                limiter = SqliteRateLimiter(connection, key_secret=b"r" * 32)  # noqa: F405
                try:
                    barrier.wait(timeout=5)
                    decisions.append(
                        limiter.consume(
                            identity="principal:service-a",
                            operation_id="getSite",
                            policy=RateLimitPolicy(1, 60),  # noqa: F405
                            now=NOW,
                        )
                    )
                except BaseException as exc:
                    errors.append(exc)
                finally:
                    connection.close()

            threads = [threading.Thread(target=run), threading.Thread(target=run)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)
            self.assertFalse(any(thread.is_alive() for thread in threads))
            self.assertEqual(errors, [])
            self.assertEqual(sum(decision.allowed for decision in decisions), 1)
            self.assertEqual(sum(not decision.allowed for decision in decisions), 1)

    def test_bucket_identity_is_hashed_and_not_persisted(self) -> None:
        connection, limiter = self.limiter()
        limiter.consume(
            identity="principal:highly-sensitive-account-name",
            operation_id="getSite",
            policy=RateLimitPolicy(10, 60),  # noqa: F405
            now=NOW,
        )
        row = connection.execute(
            "SELECT bucket_hash, operation_id FROM hp_api_http_rate_limits"
        ).fetchone()
        self.assertIsInstance(row[0], bytes)
        self.assertEqual(len(row[0]), 32)
        dump = " ".join(
            str(value)
            for value in connection.execute(
                "SELECT quote(bucket_hash), operation_id FROM hp_api_http_rate_limits"
            ).fetchone()
        )
        self.assertNotIn("sensitive", dump)
        connection.close()

    def test_rate_limit_state_respects_outer_transaction(self) -> None:
        connection, limiter = self.limiter()
        connection.execute("BEGIN")
        limiter.consume(
            identity="source:192.0.2.9",
            operation_id="getSite",
            policy=RateLimitPolicy(2, 60),  # noqa: F405
            now=NOW,
        )
        self.assertTrue(connection.in_transaction)
        connection.rollback()
        self.assertEqual(
            connection.execute("SELECT count(*) FROM hp_api_http_rate_limits").fetchone()[0],
            0,
        )
        connection.close()

    def test_schema_shape_tampering_is_detected_and_pruning_is_bounded(self) -> None:
        connection, limiter = self.limiter()
        for offset in range(3):
            limiter.consume(
                identity=f"source:192.0.2.{offset + 1}",
                operation_id="getSite",
                policy=RateLimitPolicy(2, 60),  # noqa: F405
                now=NOW - 120 + offset,
            )
        self.assertEqual(limiter.prune(before=NOW - 60, limit=2), 2)
        self.assertEqual(
            connection.execute("SELECT count(*) FROM hp_api_http_rate_limits").fetchone()[0],
            1,
        )
        connection.execute("DROP INDEX hp_api_http_rate_limits_window_idx")
        connection.execute(
            "CREATE INDEX hp_api_http_rate_limits_window_idx ON hp_api_http_rate_limits(operation_id)"
        )
        with self.assertRaises(ConfigurationError):  # noqa: F405
            limiter.migrate()
        connection.close()


if __name__ == "__main__":
    unittest.main()  # noqa: F405
