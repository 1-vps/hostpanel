from __future__ import annotations

from tests.http_test_support import *  # noqa: F403


class HostpanelApiHttpRoutingTests(unittest.TestCase):  # noqa: F405
    def test_builtin_health_and_openapi_are_versioned_json(self) -> None:
        app = ApiApplication()  # noqa: F405
        health = app.dispatch(Request("GET", "/api/v1/health"), now=NOW)  # noqa: F405
        self.assertEqual(health.status, 200)
        self.assertEqual(body(health), {"api_version": "v1", "status": "ok"})  # noqa: F405
        self.assertEqual(headers(health)["content-type"], "application/json")  # noqa: F405
        contract = app.dispatch(Request("GET", "/api/v1/openapi.json"), now=NOW)  # noqa: F405
        self.assertEqual(contract.status, 200)
        self.assertEqual(body(contract)["openapi"], "3.1.0")  # noqa: F405
        self.assertIn("/api/v1/health", body(contract)["paths"])  # noqa: F405

    def test_route_not_found_and_method_not_allowed_use_stable_errors(self) -> None:
        app = ApiApplication()  # noqa: F405
        missing = app.dispatch(Request("GET", "/api/v1/missing"), now=NOW)  # noqa: F405
        self.assertEqual(missing.status, 404)
        self.assertEqual(body(missing)["error"]["code"], "not_found")  # noqa: F405
        self.assertEqual(
            body(missing)["error"]["request_id"],  # noqa: F405
            headers(missing)["x-request-id"],  # noqa: F405
        )
        method = app.dispatch(Request("POST", "/api/v1/health"), now=NOW)  # noqa: F405
        self.assertEqual(method.status, 405)
        self.assertEqual(headers(method)["allow"], "GET, HEAD")  # noqa: F405
        self.assertEqual(body(method)["error"]["code"], "method_not_allowed")  # noqa: F405

    def test_access_controller_authenticates_before_target_resolution(self) -> None:
        recorder = AccessRecorder()  # noqa: F405
        events = recorder.events

        def resolver(principal, request, path_parameters, query_parameters):
            events.append(("resolve", principal.principal_id, path_parameters["site_id"]))
            return AuthorizationTarget("tenant-a", "site", path_parameters["site_id"])  # noqa: F405

        app = ApiApplication(access_controller=recorder.controller())  # noqa: F405
        app.register(
            Route(  # noqa: F405
                method="GET",
                path="/api/v1/sites/{site_id}",
                operation_id="getSite",
                summary="Read one site",
                required_scope="sites:read",
                resolve_target=resolver,
                handler=lambda context: events.append(("handler", context.target.resource_id))
                or {"ok": True},
            )
        )
        response = app.dispatch(
            Request(
                "GET",
                "/api/v1/sites/example.com",
                headers=(("Authorization", f"Bearer {TOKEN}"),),  # noqa: F405
                source_ip="192.0.2.4",
            ),
            now=NOW,
        )
        self.assertEqual(response.status, 200)
        self.assertEqual([event[0] for event in events], ["authenticate", "resolve", "authorize", "handler"])
        self.assertEqual(events[2][2], "sites:read")

    def test_missing_invalid_and_rejected_bearer_tokens_are_indistinguishable(self) -> None:
        app, recorder = protected_app()  # noqa: F405
        missing = app.dispatch(Request("GET", "/api/v1/sites/example.com"), now=NOW)  # noqa: F405
        self.assertEqual(missing.status, 401)
        self.assertEqual(body(missing)["error"]["code"], "authentication_required")  # noqa: F405
        malformed = app.dispatch(
            Request(
                "GET",
                "/api/v1/sites/example.com",
                headers=(("Authorization", "Basic abc"),),
            ),
            now=NOW,
        )
        self.assertEqual(malformed.status, 401)
        self.assertEqual(body(malformed)["error"]["code"], "invalid_token")  # noqa: F405
        recorder.fail_auth = True
        rejected = app.dispatch(
            Request(
                "GET",
                "/api/v1/sites/example.com",
                headers=(("Authorization", f"Bearer {TOKEN}"),),  # noqa: F405
            ),
            now=NOW,
        )
        self.assertEqual(rejected.status, 401)
        self.assertNotIn("secret", rejected.body.decode("utf-8"))
        self.assertIn("Bearer", headers(rejected)["www-authenticate"])  # noqa: F405

    def test_authorization_failure_is_stable_and_does_not_run_handler(self) -> None:
        recorder = AccessRecorder()  # noqa: F405
        recorder.fail_authorize = True
        called = []
        app, _ = protected_app(recorder=recorder, handler=lambda context: called.append(True) or {"ok": True})  # noqa: F405
        response = app.dispatch(
            Request(
                "GET",
                "/api/v1/sites/example.com",
                headers=(("Authorization", f"Bearer {TOKEN}"),),  # noqa: F405
            ),
            now=NOW,
        )
        self.assertEqual(response.status, 403)
        self.assertEqual(body(response)["error"]["code"], "forbidden")  # noqa: F405
        self.assertNotIn("secret", response.body.decode("utf-8"))
        self.assertEqual(called, [])

    def test_handler_exception_is_reported_out_of_band_and_redacted(self) -> None:
        reported = []
        recorder = AccessRecorder()  # noqa: F405
        app = ApiApplication(  # noqa: F405
            access_controller=recorder.controller(),
            error_reporter=lambda request_id, error: reported.append((request_id, str(error))),
        )
        app.register(
            Route(  # noqa: F405
                method="GET",
                path="/api/v1/sites/{site_id}",
                operation_id="getSite",
                summary="Read one site",
                required_scope="sites:read",
                resolve_target=target_resolver,  # noqa: F405
                handler=lambda context: (_ for _ in ()).throw(RuntimeError("database password leaked")),
            )
        )
        response = app.dispatch(
            Request(
                "GET",
                "/api/v1/sites/example.com",
                headers=(("Authorization", f"Bearer {TOKEN}"),),  # noqa: F405
            ),
            now=NOW,
        )
        self.assertEqual(response.status, 500)
        self.assertEqual(body(response)["error"]["code"], "internal_error")  # noqa: F405
        self.assertNotIn("password", response.body.decode("utf-8"))
        self.assertEqual(reported[0][0], headers(response)["x-request-id"])  # noqa: F405
        self.assertIn("password", reported[0][1])

    def test_json_route_and_unexpected_body_are_enforced_before_handler(self) -> None:
        received = []
        app = ApiApplication()  # noqa: F405
        app.register(
            Route(  # noqa: F405
                method="POST",
                path="/api/v1/echo",
                operation_id="createEcho",
                summary="Echo a JSON object",
                auth_required=False,
                expects_json_body=True,
                handler=lambda context: received.append(context.json_body) or context.json_body,
            )
        )
        valid = app.dispatch(
            Request(
                "POST",
                "/api/v1/echo",
                headers=(("Content-Type", "application/json; charset=utf-8"),),
                body=b'{"name":"site"}',
            ),
            now=NOW,
        )
        self.assertEqual(valid.status, 200)
        self.assertEqual(received, [{"name": "site"}])
        unexpected = app.dispatch(
            Request("GET", "/api/v1/health", body=b"{}"),
            now=NOW,
        )
        self.assertEqual(unexpected.status, 400)
        self.assertEqual(body(unexpected)["error"]["code"], "unexpected_body")  # noqa: F405

    def test_idempotency_key_is_required_validated_and_exposed_to_handler(self) -> None:
        received = []
        app = ApiApplication()  # noqa: F405
        app.register(
            Route(  # noqa: F405
                method="POST",
                path="/api/v1/mutations",
                operation_id="createMutation",
                summary="Create one mutation",
                auth_required=False,
                idempotency_required=True,
                handler=lambda context: received.append(context.idempotency_key) or {"ok": True},
            )
        )
        for request, code in (
            (Request("POST", "/api/v1/mutations"), "idempotency_key_required"),  # noqa: F405
            (
                Request(
                    "POST",
                    "/api/v1/mutations",
                    headers=(("Idempotency-Key", "too-short"),),
                ),
                "invalid_idempotency_key",
            ),
            (
                Request(
                    "POST",
                    "/api/v1/mutations",
                    headers=(
                        ("Idempotency-Key", "mutation-key-0001"),
                        ("idempotency-key", "mutation-key-0002"),
                    ),
                ),
                "duplicate_header",
            ),
        ):
            with self.subTest(code=code):
                response = app.dispatch(request, now=NOW)
                self.assertEqual(response.status, 400)
                self.assertEqual(body(response)["error"]["code"], code)  # noqa: F405
        self.assertEqual(received, [])

        accepted = app.dispatch(
            Request(
                "POST",
                "/api/v1/mutations",
                headers=(("Idempotency-Key", "mutation-key-0001"),),
            ),
            now=NOW,
        )
        self.assertEqual(accepted.status, 200)
        self.assertEqual(received, ["mutation-key-0001"])

    def test_idempotency_requirement_is_restricted_to_mutation_methods(self) -> None:
        with self.assertRaises(ConfigurationError):  # noqa: F405
            Route(  # noqa: F405
                method="GET",
                path="/api/v1/invalid-idempotency",
                operation_id="getInvalidIdempotency",
                summary="Invalid idempotency route",
                auth_required=False,
                idempotency_required=True,
                handler=lambda context: {},
            )

    def test_head_uses_get_contract_without_returning_a_body(self) -> None:
        app = ApiApplication()  # noqa: F405
        get = app.dispatch(Request("GET", "/api/v1/health"), now=NOW)  # noqa: F405
        head = app.dispatch(Request("HEAD", "/api/v1/health"), now=NOW)  # noqa: F405
        self.assertEqual(head.status, 200)
        self.assertEqual(head.body, b"")
        self.assertEqual(headers(head)["content-length"], headers(get)["content-length"])  # noqa: F405

    def test_response_status_cannot_drift_from_registered_contract(self) -> None:
        for success_status, result in (
            (200, JsonResponse({"ok": True}, status=201)),  # noqa: F405
            (200, Response(204, (), b"")),  # noqa: F405
            (204, {"ok": True}),
        ):
            with self.subTest(success_status=success_status, result=result):
                app = ApiApplication()  # noqa: F405
                app.register(
                    Route(  # noqa: F405
                        method="POST",
                        path="/api/v1/status-contract",
                        operation_id="createStatusContract",
                        summary="Check the response status contract",
                        auth_required=False,
                        success_status=success_status,
                        handler=lambda context, result=result: result,
                    )
                )
                response = app.dispatch(
                    Request("POST", "/api/v1/status-contract"),
                    now=NOW,
                )
                self.assertEqual(response.status, 500)
                self.assertEqual(body(response)["error"]["code"], "internal_error")  # noqa: F405

        app = ApiApplication()  # noqa: F405
        app.register(
            Route(  # noqa: F405
                method="DELETE",
                path="/api/v1/status-contract",
                operation_id="deleteStatusContract",
                summary="Delete with no response body",
                auth_required=False,
                success_status=204,
                handler=lambda context: Response(204, (("X-Result", "deleted"),), b""),  # noqa: F405
            )
        )
        accepted = app.dispatch(
            Request("DELETE", "/api/v1/status-contract"),
            now=NOW,
        )
        self.assertEqual(accepted.status, 204)
        self.assertEqual(accepted.body, b"")
        self.assertEqual(headers(accepted)["x-result"], "deleted")  # noqa: F405

    def test_route_table_freezes_and_rejects_ambiguous_contracts(self) -> None:
        app = ApiApplication()  # noqa: F405
        app.dispatch(Request("GET", "/api/v1/health"), now=NOW)  # noqa: F405
        with self.assertRaises(ConfigurationError):  # noqa: F405
            app.register(
                Route(  # noqa: F405
                    method="GET",
                    path="/api/v1/later",
                    operation_id="getLater",
                    summary="Late route",
                    auth_required=False,
                    handler=lambda context: {},
                )
            )
        app = ApiApplication()  # noqa: F405
        app.register(
            Route(  # noqa: F405
                method="GET",
                path="/api/v1/sites/{site_id}",
                operation_id="getSite",
                summary="One",
                auth_required=False,
                handler=lambda context: {},
            )
        )
        with self.assertRaises(ConfigurationError):  # noqa: F405
            app.register(
                Route(  # noqa: F405
                    method="GET",
                    path="/api/v1/sites/{other_id}",
                    operation_id="getOtherSite",
                    summary="Two",
                    auth_required=False,
                    handler=lambda context: {},
                )
            )


if __name__ == "__main__":
    unittest.main()  # noqa: F405
