from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .model import ConfigurationError, clean_text


def generate_openapi(
    routes: Iterable[Any],
    *,
    title: str = "HostPanel API",
    version: str = "1.0.0",
) -> Mapping[str, Any]:
    api_title = clean_text(title, "OpenAPI title", maximum=120)
    api_version = clean_text(version, "OpenAPI version", maximum=40)
    paths: dict[str, dict[str, Any]] = {}
    seen_operations: set[str] = set()
    for route in sorted(routes, key=lambda item: (item.path, item.method)):
        if route.operation_id in seen_operations:
            raise ConfigurationError("OpenAPI operation IDs must be unique")
        seen_operations.add(route.operation_id)
        parameters: list[Mapping[str, Any]] = [
            {"$ref": "#/components/parameters/RequestIdHeader"}
        ]
        for name in route.path_parameter_names:
            parameters.append(
                {
                    "in": "path",
                    "name": name,
                    "required": True,
                    "schema": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 128,
                        "pattern": "^[A-Za-z0-9][A-Za-z0-9_.:@+~-]{0,127}$",
                    },
                }
            )
        parameters.extend(dict(parameter) for parameter in route.query_parameters)
        if route.idempotency_required:
            parameters.append(
                {"$ref": "#/components/parameters/IdempotencyKeyHeader"}
            )
        operation: dict[str, Any] = {
            "operationId": route.operation_id,
            "summary": route.summary,
            "responses": _route_responses(route),
            "tags": list(route.tags),
            "parameters": parameters,
        }
        if route.auth_required:
            operation["security"] = [{"bearerAuth": []}]
            operation["x-hostpanel-required-scope"] = route.required_scope
        else:
            operation["security"] = []
        if route.expects_json_body:
            operation["requestBody"] = {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": route.request_schema or {"type": "object"}
                    }
                },
            }
        if route.source_rate_limit is not None:
            operation["x-hostpanel-source-rate-limit"] = {
                "limit": route.source_rate_limit.limit,
                "window_seconds": route.source_rate_limit.window_seconds,
            }
        if route.principal_rate_limit is not None:
            operation["x-hostpanel-principal-rate-limit"] = {
                "limit": route.principal_rate_limit.limit,
                "window_seconds": route.principal_rate_limit.window_seconds,
            }
        paths.setdefault(route.path, {})[route.method.lower()] = operation
    return {
        "openapi": "3.1.0",
        "info": {
            "title": api_title,
            "version": api_version,
            "description": (
                "Generated from the HostPanel /api/v1 route registry. "
                "Only registered operations are part of the contract."
            ),
        },
        "jsonSchemaDialect": "https://json-schema.org/draft/2020-12/schema",
        "paths": paths,
        "components": {
            "parameters": {
                "RequestIdHeader": {
                    "in": "header",
                    "name": "X-Request-Id",
                    "required": False,
                    "schema": {
                        "type": "string",
                        "minLength": 8,
                        "maxLength": 128,
                        "pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$",
                    },
                    "description": (
                        "Optional caller-supplied correlation ID. The response always "
                        "contains the accepted or generated value."
                    ),
                },
                "IdempotencyKeyHeader": {
                    "in": "header",
                    "name": "Idempotency-Key",
                    "required": True,
                    "schema": {
                        "type": "string",
                        "minLength": 16,
                        "maxLength": 128,
                        "pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$",
                    },
                    "description": (
                        "Caller-generated mutation key. The signed application adapter "
                        "must bind it to principal, method, route, and request body using "
                        "the durable control-plane primitive from PR #153."
                    ),
                },
            },
            "schemas": {
                "ApiError": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["code", "message", "request_id"],
                    "properties": {
                        "code": {"type": "string"},
                        "message": {"type": "string"},
                        "request_id": {"type": "string"},
                        "details": {"type": "object"},
                    },
                },
                "ApiErrorEnvelope": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["error"],
                    "properties": {
                        "error": {"$ref": "#/components/schemas/ApiError"}
                    },
                },
            },
            "responses": {
                "BadRequest": _error_response("The request is invalid"),
                "Unauthorized": _error_response("Authentication failed"),
                "Forbidden": _error_response("Authorization failed"),
                "NotFound": _error_response("The route or resource was not found"),
                "MethodNotAllowed": _error_response("The method is not allowed"),
                "Conflict": _error_response("The request conflicts with current state"),
                "RateLimited": _error_response("The rate limit was exceeded", retry=True),
                "PayloadTooLarge": _error_response("The request body is too large"),
                "UnsupportedMediaType": _error_response("The media type is unsupported"),
                "InternalError": _error_response("The request could not be completed"),
            },
            "securitySchemes": {
                "bearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "bearerFormat": "HostPanel API token",
                    "description": (
                        "Service-account bearer token. Interactive sessions and "
                        "impersonation use separate signed-application adapters."
                    ),
                }
            },
        },
    }


def _route_responses(route: Any) -> Mapping[str, Any]:
    success_schema = route.response_schema or {"type": "object"}
    success_response: dict[str, Any] = {
        "description": "Successful response",
        "headers": {"X-Request-Id": {"schema": {"type": "string"}}},
    }
    if route.success_status not in {204, 205}:
        success_response["content"] = {
            "application/json": {"schema": success_schema}
        }
    responses: dict[str, Any] = {
        str(route.success_status): success_response,
        "400": {"$ref": "#/components/responses/BadRequest"},
        "404": {"$ref": "#/components/responses/NotFound"},
        "405": {"$ref": "#/components/responses/MethodNotAllowed"},
        "409": {"$ref": "#/components/responses/Conflict"},
        "429": {"$ref": "#/components/responses/RateLimited"},
        "500": {"$ref": "#/components/responses/InternalError"},
    }
    if route.auth_required:
        responses["401"] = {"$ref": "#/components/responses/Unauthorized"}
        responses["403"] = {"$ref": "#/components/responses/Forbidden"}
    if route.expects_json_body:
        responses["413"] = {"$ref": "#/components/responses/PayloadTooLarge"}
        responses["415"] = {"$ref": "#/components/responses/UnsupportedMediaType"}
    return responses


def _error_response(description: str, *, retry: bool = False) -> Mapping[str, Any]:
    headers: dict[str, Any] = {
        "X-Request-Id": {"schema": {"type": "string"}}
    }
    if retry:
        headers["Retry-After"] = {"schema": {"type": "integer", "minimum": 1}}
    return {
        "description": description,
        "headers": headers,
        "content": {
            "application/json": {
                "schema": {"$ref": "#/components/schemas/ApiErrorEnvelope"}
            }
        },
    }
