from __future__ import annotations

from tests.http_test_support import *  # noqa: F403
from hostpanel_api_http.jsonio import parse_json_object
from hostpanel_api_http.auth import parse_bearer_authorization


class HostpanelApiHttpSecurityRequestTests(unittest.TestCase):  # noqa: F405
    def test_json_parser_rejects_duplicates_nonfinite_nonobject_and_wrong_media(self) -> None:
        invalid = (
            (b'{"a":1,"a":2}', "application/json", "invalid_json"),
            (b'{"a":NaN}', "application/json", "invalid_json"),
            (b'[]', "application/json", "invalid_json"),
            (b'{}', "text/plain", "unsupported_media_type"),
            (b'{}', None, "unsupported_media_type"),
        )
        for payload, content_type, code in invalid:
            with self.subTest(payload=payload, content_type=content_type), self.assertRaises(ApiProblem) as caught:  # noqa: F405
                parse_json_object(payload, content_type)
            self.assertEqual(caught.exception.code, code)

    def test_json_parser_enforces_size_depth_and_integer_bounds(self) -> None:
        with self.assertRaises(ApiProblem) as caught:  # noqa: F405
            parse_json_object(b'{"a":"12345"}', "application/json", max_bytes=5)
        self.assertEqual(caught.exception.status, 413)
        nested = b'{"a":' + b'[' * 40 + b'0' + b']' * 40 + b'}'
        with self.assertRaises(ApiProblem) as caught:  # noqa: F405
            parse_json_object(nested, "application/json")
        self.assertEqual(caught.exception.code, "invalid_json")
        with self.assertRaises(ApiProblem):  # noqa: F405
            parse_json_object(b'{"a":9223372036854775808}', "application/json")

    def test_path_normalization_rejects_aliases_traversal_and_encoded_slashes(self) -> None:
        app = ApiApplication()  # noqa: F405
        for path in (
            "/api/v1//health",
            "/api/v1/./health",
            "/api/v1/%2e%2e/health",
            "/api/v1%2fhealth",
            "/api/v1\\health",
            "/api/v1/health/",
            "/api/v1/%ZZ",
        ):
            with self.subTest(path=path):
                response = app.dispatch(Request("GET", path), now=NOW)  # noqa: F405
                self.assertEqual(response.status, 400)
                self.assertEqual(body(response)["error"]["code"], "invalid_path")  # noqa: F405

    def test_invalid_query_and_duplicate_security_headers_fail_closed(self) -> None:
        app = ApiApplication()  # noqa: F405
        query = app.dispatch(
            Request("GET", "/api/v1/health", query_string="cursor=%ZZ"),
            now=NOW,
        )
        self.assertEqual(query.status, 400)
        self.assertEqual(body(query)["error"]["code"], "invalid_query")  # noqa: F405
        duplicate = app.dispatch(
            Request(
                "GET",
                "/api/v1/health",
                headers=(("X-Request-Id", "request-1234"), ("x-request-id", "request-5678")),
            ),
            now=NOW,
        )
        self.assertEqual(duplicate.status, 400)
        self.assertEqual(body(duplicate)["error"]["code"], "duplicate_header")  # noqa: F405

    def test_request_id_is_echoed_only_when_valid(self) -> None:
        app = ApiApplication()  # noqa: F405
        accepted = app.dispatch(
            Request(
                "GET",
                "/api/v1/health",
                headers=(("X-Request-Id", "client-request-1234"),),
            ),
            now=NOW,
        )
        self.assertEqual(headers(accepted)["x-request-id"], "client-request-1234")  # noqa: F405
        invalid = app.dispatch(
            Request(
                "GET",
                "/api/v1/health",
                headers=(("X-Request-Id", "bad id"),),
            ),
            now=NOW,
        )
        self.assertEqual(invalid.status, 400)
        self.assertNotEqual(headers(invalid)["x-request-id"], "bad id")  # noqa: F405
        self.assertEqual(body(invalid)["error"]["code"], "invalid_request_id")  # noqa: F405

    def test_bearer_parser_rejects_ambiguous_or_duplicate_values(self) -> None:
        invalid_headers = (
            (("Authorization", " Bearer " + TOKEN),),
            (("Authorization", "Bearer  " + TOKEN),),
            (("Authorization", "bearer short"),),
            (("Authorization", "Bearer " + TOKEN + ",other"),),
            (("Authorization", "Bearer " + TOKEN), ("authorization", "Bearer " + TOKEN)),
        )
        for values in invalid_headers:
            with self.subTest(values=values), self.assertRaises(ApiProblem):  # noqa: F405
                parse_bearer_authorization(Request("GET", "/api/v1/health", headers=values))  # noqa: F405
        valid = parse_bearer_authorization(
            Request(
                "GET",
                "/api/v1/health",
                headers=(("Authorization", "bEaReR " + TOKEN),),
            )
        )
        self.assertEqual(valid, TOKEN)

    def test_handlers_cannot_set_cookies_override_security_headers_or_send_raw_json(self) -> None:
        for result in (
            JsonResponse({"ok": True}, headers={"Set-Cookie": "session=secret"}),  # noqa: F405
            JsonResponse({"ok": True}, headers={"X-Request-Id": "forged-id"}),  # noqa: F405
            Response(200, (("Content-Type", "application/json"),), b'{"raw":true}'),  # noqa: F405
        ):
            with self.subTest(result=result):
                app = ApiApplication()  # noqa: F405
                app.register(
                    Route(  # noqa: F405
                        method="GET",
                        path="/api/v1/custom",
                        operation_id="getCustom",
                        summary="Custom response",
                        auth_required=False,
                        handler=lambda context, result=result: result,
                    )
                )
                response = app.dispatch(Request("GET", "/api/v1/custom"), now=NOW)  # noqa: F405
                self.assertEqual(response.status, 500)
                self.assertEqual(body(response)["error"]["code"], "internal_error")  # noqa: F405

    def test_pagination_parser_is_bounded_and_rejects_ambiguous_values(self) -> None:
        self.assertEqual(parse_page_query({}).limit, 50)  # noqa: F405
        parsed = parse_page_query(  # noqa: F405
            {"cursor": ("signed-cursor",), "limit": ("25",)},
            default_limit=10,
            max_limit=100,
        )
        self.assertEqual((parsed.cursor, parsed.limit), ("signed-cursor", 25))
        invalid = (
            {"cursor": ("one", "two")},
            {"limit": ("1", "2")},
            {"limit": ("0",)},
            {"limit": ("01",)},
            {"limit": ("101",)},
            {"limit": ("-1",)},
            {"cursor": ("",)},
        )
        for query in invalid:
            with self.subTest(query=query), self.assertRaises(ApiProblem):  # noqa: F405
                parse_page_query(query, max_limit=100)  # noqa: F405

    def test_unsafe_public_problem_content_falls_back_to_generic_500(self) -> None:
        app = ApiApplication()  # noqa: F405
        app.register(
            Route(  # noqa: F405
                method="GET",
                path="/api/v1/problem",
                operation_id="getUnsafeProblem",
                summary="Raise an unsafe public problem",
                auth_required=False,
                handler=lambda context: (_ for _ in ()).throw(
                    ApiProblem(
                        400,
                        "unsafe_problem",
                        "Unsafe problem",
                        details={"value": object()},
                        headers={"X-Unsafe": "line\nbreak"},
                    )
                ),
            )
        )
        response = app.dispatch(Request("GET", "/api/v1/problem"), now=NOW)  # noqa: F405
        self.assertEqual(response.status, 500)
        self.assertEqual(body(response)["error"]["code"], "internal_error")  # noqa: F405
        self.assertNotIn("unsafe", response.body.decode("utf-8").lower())



if __name__ == "__main__":
    unittest.main()  # noqa: F405
