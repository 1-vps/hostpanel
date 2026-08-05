from __future__ import annotations

import secrets
from collections.abc import Mapping

from .auth import parse_bearer_authorization
from .jsonio import parse_json_object
from .model import (
    ApiProblem,
    ConfigurationError,
    PrincipalContext,
    RateLimitDecision,
    RateLimitPolicy,
    Request,
    RequestContext,
    Response,
    REQUEST_ID_RE,
    unix_time,
)
from .request_parsing import _normalize_path, _parse_query, _rate_limit_problem
from .route_selection import RouteSelectionMixin

_IDEMPOTENCY_KEY_RE = __import__('re').compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{15,127}\Z")


class DispatchMixin(RouteSelectionMixin):
    def dispatch(self, request: Request, *, now: int | None = None) -> Response:
        if not isinstance(request, Request):
            raise TypeError("request must be Request")
        timestamp = unix_time(now)
        if not self._frozen:
            self.freeze()
        request_id = "req_" + secrets.token_urlsafe(16)
        rate_decisions: list[RateLimitDecision] = []
        head_request = request.method == "HEAD"
        try:
            supplied_request_id = request.single_header("X-Request-Id")
            if supplied_request_id is not None:
                if REQUEST_ID_RE.fullmatch(supplied_request_id) is None:
                    raise ApiProblem(
                        400,
                        "invalid_request_id",
                        "The request ID is invalid.",
                    )
                request_id = supplied_request_id
            path = _normalize_path(request.raw_path)
            query = _parse_query(request.query_string)
            registered, path_parameters = self._select_route(path, request.method)
            route = registered.route
            if route.source_rate_limit is not None:
                decision = self._consume_rate_limit(
                    identity="source:" + request.source_ip,
                    route=route,
                    policy=route.source_rate_limit,
                    now=timestamp,
                )
                rate_decisions.append(decision)
                if not decision.allowed:
                    raise _rate_limit_problem(decision)
            idempotency_key = None
            if route.idempotency_required:
                idempotency_key = request.single_header("Idempotency-Key")
                if idempotency_key is None:
                    raise ApiProblem(
                        400,
                        "idempotency_key_required",
                        "This operation requires an Idempotency-Key header.",
                    )
                if _IDEMPOTENCY_KEY_RE.fullmatch(idempotency_key) is None:
                    raise ApiProblem(
                        400,
                        "invalid_idempotency_key",
                        "The idempotency key is invalid.",
                    )
            json_body: Mapping[str, Any] | None = None
            if route.expects_json_body:
                content_type = request.single_header("Content-Type")
                json_body = parse_json_object(
                    request.body,
                    content_type,
                    max_bytes=route.max_body_bytes,
                )
            elif request.body:
                raise ApiProblem(
                    400,
                    "unexpected_body",
                    "This operation does not accept a request body.",
                )
            principal: PrincipalContext | None = None
            target = None
            if route.auth_required:
                if self.access_controller is None:
                    raise ConfigurationError(
                        "protected API routes require an access controller"
                    )
                raw_token = parse_bearer_authorization(request)
                grant = self.access_controller.authorize(
                    raw_token=raw_token,
                    required_scope=route.required_scope,
                    request=request,
                    path_parameters=path_parameters,
                    query_parameters=query,
                    resolve_target=route.resolve_target,
                    now=timestamp,
                )
                principal = grant.principal
                target = grant.target
                if route.principal_rate_limit is not None:
                    decision = self._consume_rate_limit(
                        identity="principal:" + principal.principal_id,
                        route=route,
                        policy=route.principal_rate_limit,
                        now=timestamp,
                    )
                    rate_decisions.append(decision)
                    if not decision.allowed:
                        raise _rate_limit_problem(decision)
            context = RequestContext(
                request=request,
                request_id=request_id,
                path_parameters=path_parameters,
                query_parameters=query,
                principal=principal,
                target=target,
                idempotency_key=idempotency_key,
                json_body=json_body,
            )
            result = route.handler(context)
            response = self._render_result(result, route.success_status, request_id, rate_decisions)
        except ApiProblem as problem:
            response = self.render_problem(
                problem,
                request_id=request_id,
                rate_decisions=rate_decisions,
            )
        except Exception as exc:
            if self.error_reporter is not None:
                try:
                    self.error_reporter(request_id, exc)
                except Exception:
                    pass
            response = self.render_problem(
                ApiProblem(500, "internal_error", "The request could not be completed."),
                request_id=request_id,
                rate_decisions=rate_decisions,
            )
        if head_request:
            return Response(response.status, response.headers, b"")
        return response
