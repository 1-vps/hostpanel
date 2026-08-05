from __future__ import annotations

from tests.http_test_support import *  # noqa: F403
from hostpanel_api_http.jsonio import canonical_json_bytes
import subprocess
import sys


class HostpanelApiHttpOpenApiTests(unittest.TestCase):  # noqa: F405
    def contract_app(self):
        app, _ = protected_app(  # noqa: F405
            route_options={
                "method": "POST",
                "operation_id": "updateSite",
                "summary": "Update one site",
                "expects_json_body": True,
                "idempotency_required": True,
                "request_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["enabled"],
                    "properties": {"enabled": {"type": "boolean"}},
                },
                "source_rate_limit": RateLimitPolicy(20, 60),  # noqa: F405
                "principal_rate_limit": RateLimitPolicy(10, 60),  # noqa: F405
            },
            rate_limiter=object(),
        )
        return app

    def test_contract_is_generated_from_registered_routes(self) -> None:
        app = self.contract_app()
        document = generate_openapi(app.routes, title=app.title, version=app.version)  # noqa: F405
        operation = document["paths"]["/api/v1/sites/{site_id}"]["post"]
        self.assertEqual(document["openapi"], "3.1.0")
        self.assertEqual(operation["operationId"], "updateSite")
        self.assertEqual(operation["security"], [{"bearerAuth": []}])
        self.assertEqual(operation["x-hostpanel-required-scope"], "sites:read")
        self.assertEqual(operation["requestBody"]["content"]["application/json"]["schema"]["required"], ["enabled"])
        self.assertEqual(operation["x-hostpanel-source-rate-limit"], {"limit": 20, "window_seconds": 60})
        self.assertEqual(operation["x-hostpanel-principal-rate-limit"], {"limit": 10, "window_seconds": 60})
        self.assertIn({"$ref": "#/components/parameters/IdempotencyKeyHeader"}, operation["parameters"])
        self.assertEqual(operation["responses"]["409"], {"$ref": "#/components/responses/Conflict"})
        path_parameters = [
            item for item in operation["parameters"] if item.get("in") == "path"
        ]
        self.assertEqual([item["name"] for item in path_parameters], ["site_id"])

    def test_empty_success_response_has_no_json_content_contract(self) -> None:
        app = ApiApplication()  # noqa: F405
        app.register(
            Route(  # noqa: F405
                method="DELETE",
                path="/api/v1/items/{item_id}",
                operation_id="deleteItem",
                summary="Delete one item",
                auth_required=False,
                success_status=204,
                handler=lambda context: Response(204, (), b""),  # noqa: F405
            )
        )
        response = generate_openapi(app.routes)["paths"]["/api/v1/items/{item_id}"]["delete"]["responses"]["204"]  # noqa: F405
        self.assertNotIn("content", response)
        self.assertIn("X-Request-Id", response["headers"])

    def test_public_routes_explicitly_disable_security(self) -> None:
        app = ApiApplication()  # noqa: F405
        document = generate_openapi(app.routes)  # noqa: F405
        self.assertEqual(document["paths"]["/api/v1/health"]["get"]["security"], [])
        self.assertEqual(document["paths"]["/api/v1/openapi.json"]["get"]["security"], [])
        self.assertIn("bearerAuth", document["components"]["securitySchemes"])

    def test_error_responses_and_request_id_header_are_shared_contracts(self) -> None:
        document = generate_openapi(ApiApplication().routes)  # noqa: F405
        responses = document["components"]["responses"]
        for name in (
            "BadRequest",
            "Unauthorized",
            "Forbidden",
            "NotFound",
            "MethodNotAllowed",
            "Conflict",
            "RateLimited",
            "PayloadTooLarge",
            "UnsupportedMediaType",
            "InternalError",
        ):
            self.assertIn(name, responses)
        self.assertIn("Retry-After", responses["RateLimited"]["headers"])
        self.assertIn("RequestIdHeader", document["components"]["parameters"])
        self.assertIn("IdempotencyKeyHeader", document["components"]["parameters"])

    def test_dynamic_openapi_endpoint_matches_generator_exactly(self) -> None:
        app = ApiApplication()  # noqa: F405
        expected = canonical_json_bytes(
            generate_openapi(app.routes, title=app.title, version=app.version)  # noqa: F405
        )
        actual = app.dispatch(Request("GET", "/api/v1/openapi.json"), now=NOW)  # noqa: F405
        self.assertEqual(actual.body, expected)

    def test_generator_cli_is_reproducible(self) -> None:
        command = [sys.executable, str(ROOT / "tools" / "generate_hostpanel_openapi.py")]  # noqa: F405
        first = subprocess.check_output(command, cwd=ROOT)  # noqa: F405
        second = subprocess.check_output(command, cwd=ROOT)  # noqa: F405
        expected = canonical_json_bytes(generate_openapi(ApiApplication().routes)) + b"\n"  # noqa: F405
        self.assertEqual(first, second)
        self.assertEqual(first, expected)

    def test_only_registered_routes_appear_in_the_contract(self) -> None:
        app = ApiApplication()  # noqa: F405
        document = generate_openapi(app.routes)  # noqa: F405
        self.assertEqual(
            set(document["paths"]),
            {"/api/v1/health", "/api/v1/openapi.json"},
        )
        serialized = canonical_json_bytes(document)
        self.assertNotIn(b"/api/v1/sites", serialized)
        self.assertNotIn(b"/api/v1/jobs", serialized)

    def test_protected_route_requires_scope_and_trusted_target_resolver(self) -> None:
        with self.assertRaises(ConfigurationError):  # noqa: F405
            Route(  # noqa: F405
                method="GET",
                path="/api/v1/protected",
                operation_id="getProtected",
                summary="Protected",
                handler=lambda context: {},
            )
        with self.assertRaises(ConfigurationError):  # noqa: F405
            Route(  # noqa: F405
                method="GET",
                path="/api/v1/protected",
                operation_id="getProtected",
                summary="Protected",
                required_scope="sites:read",
                handler=lambda context: {},
            )

    def test_contract_generation_is_deterministic(self) -> None:
        app = ApiApplication()  # noqa: F405
        first = canonical_json_bytes(generate_openapi(app.routes))  # noqa: F405
        second = canonical_json_bytes(generate_openapi(tuple(reversed(app.routes))))  # noqa: F405
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()  # noqa: F405
