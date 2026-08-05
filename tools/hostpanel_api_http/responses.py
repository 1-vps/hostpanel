from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .jsonio import canonical_json_bytes
from .model import (
    ApiProblem,
    ConfigurationError,
    JsonResponse,
    RateLimitDecision,
    RequestContext,
    Response,
)
from .response_headers import ResponseHeadersMixin


class ResponseMixin(ResponseHeadersMixin):
    def render_problem(
        self,
        problem: ApiProblem,
        *,
        request_id: str,
        rate_decisions: list[RateLimitDecision] | tuple[RateLimitDecision, ...] = (),
    ) -> Response:
        try:
            payload: dict[str, Any] = {
                "error": {
                    "code": problem.code,
                    "message": problem.message,
                    "request_id": request_id,
                }
            }
            if problem.details:
                payload["error"]["details"] = problem.details
            return self._json_response(
                status=problem.status,
                payload=payload,
                custom_headers=dict(problem.headers),
                request_id=request_id,
                rate_decisions=rate_decisions,
            )
        except Exception:
            payload = {
                "error": {
                    "code": "internal_error",
                    "message": "The request could not be completed.",
                    "request_id": request_id,
                }
            }
            body = canonical_json_bytes(payload)
            headers = self._base_headers(request_id)
            headers["Content-Type"] = "application/json"
            headers["Content-Length"] = str(len(body))
            return Response(500, tuple(headers.items()), body)

    def _render_result(
        self,
        result: Mapping[str, Any] | JsonResponse | Response,
        success_status: int,
        request_id: str,
        rate_decisions: list[RateLimitDecision],
    ) -> Response:
        if isinstance(result, Response):
            if result.status != success_status or result.status != 204 or result.body:
                raise ConfigurationError(
                    "route handlers may return Response only for the declared empty 204 response"
                )
            return self._empty_response(
                status=204,
                custom_headers=dict(result.headers),
                request_id=request_id,
                rate_decisions=rate_decisions,
            )
        if isinstance(result, JsonResponse):
            if result.status != success_status:
                raise ConfigurationError(
                    "JSON response status must match the registered route contract"
                )
            return self._json_response(
                status=result.status,
                payload=result.payload,
                custom_headers=result.headers,
                request_id=request_id,
                rate_decisions=rate_decisions,
            )
        if isinstance(result, Mapping):
            if success_status in {204, 205}:
                raise ConfigurationError(
                    "empty success routes must return an empty Response"
                )
            return self._json_response(
                status=success_status,
                payload=result,
                custom_headers={},
                request_id=request_id,
                rate_decisions=rate_decisions,
            )
        raise ConfigurationError(
            "route handlers must return a mapping, JsonResponse, or Response"
        )

    def _json_response(
        self,
        *,
        status: int,
        payload: Mapping[str, Any],
        custom_headers: Mapping[str, str],
        request_id: str,
        rate_decisions: list[RateLimitDecision] | tuple[RateLimitDecision, ...],
    ) -> Response:
        return self._raw_json_response(
            status=status,
            body=canonical_json_bytes(payload),
            custom_headers=custom_headers,
            request_id=request_id,
            rate_decisions=rate_decisions,
        )

    def _raw_json_response(
        self,
        *,
        status: int,
        body: bytes,
        custom_headers: Mapping[str, str],
        request_id: str,
        rate_decisions: list[RateLimitDecision] | tuple[RateLimitDecision, ...],
    ) -> Response:
        headers = self._base_headers(request_id)
        headers["Content-Type"] = "application/json"
        self._merge_custom_headers(headers, custom_headers)
        self._merge_rate_headers(headers, rate_decisions)
        headers["Content-Length"] = str(len(body))
        return Response(status, tuple(headers.items()), body)

    def _empty_response(
        self,
        *,
        status: int,
        custom_headers: Mapping[str, str],
        request_id: str,
        rate_decisions: list[RateLimitDecision],
    ) -> Response:
        headers = self._base_headers(request_id)
        self._merge_custom_headers(headers, custom_headers)
        self._merge_rate_headers(headers, rate_decisions)
        headers["Content-Length"] = "0"
        return Response(status, tuple(headers.items()), b"")

    @staticmethod
    def _health(context: RequestContext) -> Mapping[str, Any]:
        return {"api_version": "v1", "status": "ok"}

    def _openapi(self, context: RequestContext) -> Mapping[str, Any]:
        from .openapi import generate_openapi

        return generate_openapi(
            self.routes,
            title=self.title,
            version=self.version,
        )
