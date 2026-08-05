from __future__ import annotations

from tests.http_test_support import *  # noqa: F403


class HostpanelApiHttpSecurityCursorTests(unittest.TestCase):  # noqa: F405
    def test_cursor_is_signed_expiring_and_bound_to_operation_principal_and_tenant(self) -> None:
        codec = CursorCodec(b"c" * 32, ttl_seconds=60)  # noqa: F405
        cursor = codec.encode(
            {"after_id": "site-10"},
            operation="listSites",
            principal_id="service-a",
            tenant_id="tenant-a",
            now=NOW,
        )
        self.assertEqual(
            codec.decode(
                cursor,
                operation="listSites",
                principal_id="service-a",
                tenant_id="tenant-a",
                now=NOW + 1,
            ),
            {"after_id": "site-10"},
        )
        tampered = cursor[:-1] + ("A" if cursor[-1] != "A" else "B")
        for values in (
            {"cursor": tampered},
            {"cursor": cursor, "operation": "listJobs"},
            {"cursor": cursor, "principal_id": "service-b"},
            {"cursor": cursor, "tenant_id": "tenant-b"},
            {"cursor": cursor, "now": NOW + 60},
        ):
            request = {
                "cursor": cursor,
                "operation": "listSites",
                "principal_id": "service-a",
                "tenant_id": "tenant-a",
                "now": NOW + 1,
            }
            request.update(values)
            with self.subTest(request=request), self.assertRaises(ApiProblem):  # noqa: F405
                codec.decode(**request)

    def test_cursor_rejects_future_expiry_extension_even_with_valid_signature_secret(self) -> None:
        short = CursorCodec(b"d" * 32, ttl_seconds=60)  # noqa: F405
        long = CursorCodec(b"d" * 32, ttl_seconds=3600)  # noqa: F405
        cursor = long.encode(
            {"after": 1},
            operation="listSites",
            principal_id="service-a",
            tenant_id="tenant-a",
            now=NOW,
        )
        with self.assertRaises(ApiProblem) as caught:  # noqa: F405
            short.decode(
                cursor,
                operation="listSites",
                principal_id="service-a",
                tenant_id="tenant-a",
                now=NOW,
            )
        self.assertEqual(caught.exception.code, "invalid_cursor")


if __name__ == "__main__":
    unittest.main()  # noqa: F405
