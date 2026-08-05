from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any

from hostpanel_api_http.jsonio import canonical_json_bytes
from hostpanel_api_http.model import ApiProblem, JsonResponse, RequestContext

from .stores import StoreBundle


class IdempotentMutations:
    def __init__(self, stores: StoreBundle, *, ttl_seconds: int = 24 * 60 * 60) -> None:
        if not isinstance(ttl_seconds, int) or isinstance(ttl_seconds, bool) or not 60 <= ttl_seconds <= 24 * 60 * 60:
            raise ValueError("idempotency TTL is invalid")
        self.stores = stores
        self.ttl_seconds = ttl_seconds

    def execute(
        self,
        context: RequestContext,
        *,
        status: int,
        callback: Callable[[], Mapping[str, Any]],
        headers: Mapping[str, str] | None = None,
    ) -> JsonResponse:
        if context.principal is None or context.idempotency_key is None:
            raise RuntimeError("idempotent mutation requires an authenticated principal and key")
        response_headers = {} if headers is None else dict(headers)
        with self.stores.atomic():
            try:
                decision = self.stores.control.begin_idempotent_request(
                    principal_id=context.principal.principal_id,
                    key=context.idempotency_key,
                    http_method=context.request.method,
                    request_route=context.request.raw_path,
                    request_body=context.request.body,
                    expires_at=context.now + self.ttl_seconds,
                    now=context.now,
                )
            except Exception as exc:
                from .domain import map_domain_error

                raise map_domain_error(exc) from exc
            if not decision.created:
                if decision.state != "completed" or decision.response_body is None:
                    raise ApiProblem(409, "idempotency_in_progress", "An equivalent request is already in progress.")
                if decision.response_status != status:
                    raise RuntimeError("persisted idempotent response violates the route contract")
                try:
                    payload = json.loads(decision.response_body.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise RuntimeError("persisted idempotent response is invalid") from exc
                if not isinstance(payload, dict) or canonical_json_bytes(payload) != decision.response_body:
                    raise RuntimeError("persisted idempotent response is not canonical")
                replay_headers = dict(decision.response_headers or {})
                replay_headers["Idempotency-Replayed"] = "true"
                return JsonResponse(payload, status=status, headers=replay_headers)
            payload = dict(callback())
            encoded = canonical_json_bytes(payload)
            self.stores.control.complete_idempotent_request(
                decision.record_id,
                response_status=status,
                response_body=encoded,
                response_headers_map=response_headers,
                now=context.now,
            )
            return JsonResponse(payload, status=status, headers=response_headers)

    def execute_one_time_secret(
        self,
        context: RequestContext,
        *,
        status: int,
        callback: Callable[[], tuple[Mapping[str, Any], Mapping[str, Any]]],
        headers: Mapping[str, str] | None = None,
    ) -> JsonResponse:
        """Persist only a sanitized replay body, never a one-time secret."""
        if context.principal is None or context.idempotency_key is None:
            raise RuntimeError("one-time secret mutation requires a principal and key")
        response_headers = {} if headers is None else dict(headers)
        with self.stores.atomic():
            try:
                decision = self.stores.control.begin_idempotent_request(
                    principal_id=context.principal.principal_id,
                    key=context.idempotency_key,
                    http_method=context.request.method,
                    request_route=context.request.raw_path,
                    request_body=context.request.body,
                    expires_at=context.now + self.ttl_seconds,
                    now=context.now,
                )
            except Exception as exc:
                from .domain import map_domain_error

                raise map_domain_error(exc) from exc
            if not decision.created:
                if decision.state != "completed" or decision.response_body is None:
                    raise ApiProblem(
                        409,
                        "idempotency_in_progress",
                        "An equivalent request is already in progress.",
                    )
                if decision.response_status != status:
                    raise RuntimeError(
                        "persisted one-time response violates the route contract"
                    )
                try:
                    replay = json.loads(decision.response_body.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise RuntimeError("persisted one-time replay is invalid") from exc
                if not isinstance(replay, dict) or canonical_json_bytes(replay) != decision.response_body:
                    raise RuntimeError("persisted one-time replay is not canonical")
                replay_headers = dict(decision.response_headers or {})
                replay_headers["Idempotency-Replayed"] = "true"
                return JsonResponse(replay, status=status, headers=replay_headers)
            first_payload, replay_payload = callback()
            first = dict(first_payload)
            replay = dict(replay_payload)
            if canonical_json_bytes(first) == canonical_json_bytes(replay):
                raise RuntimeError("one-time secret replay must omit the disclosed secret")
            encoded_replay = canonical_json_bytes(replay)
            self.stores.control.complete_idempotent_request(
                decision.record_id,
                response_status=status,
                response_body=encoded_replay,
                response_headers_map=response_headers,
                now=context.now,
            )
            return JsonResponse(first, status=status, headers=response_headers)
