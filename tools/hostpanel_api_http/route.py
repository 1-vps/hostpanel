from __future__ import annotations

import dataclasses
import re
from collections.abc import Callable, Mapping
from typing import Any

from .jsonio import DEFAULT_MAX_BODY_BYTES
from .path import compile_path
from .model import (
    ConfigurationError,
    JsonResponse,
    RateLimitPolicy,
    RequestContext,
    Response,
    TargetResolver,
    clean_text,
    operation_id,
    scope,
)

Handler = Callable[[RequestContext], Mapping[str, Any] | JsonResponse | Response]
ErrorReporter = Callable[[str, Exception], None]


@dataclasses.dataclass(frozen=True, slots=True)
class Route:
    method: str
    path: str
    operation_id: str
    summary: str
    handler: Handler
    tags: tuple[str, ...] = ()
    auth_required: bool = True
    required_scope: str | None = None
    resolve_target: TargetResolver | None = None
    expects_json_body: bool = False
    idempotency_required: bool = False
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES
    request_schema: Mapping[str, Any] | None = None
    response_schema: Mapping[str, Any] | None = None
    query_parameters: tuple[Mapping[str, Any], ...] = ()
    success_status: int = 200
    source_rate_limit: RateLimitPolicy | None = None
    principal_rate_limit: RateLimitPolicy | None = None

    def __post_init__(self) -> None:
        method = self.method.upper()
        if re.fullmatch(r"[A-Z]{3,12}", method) is None:
            raise ConfigurationError("route method is invalid")
        compile_path(self.path)
        operation = operation_id(self.operation_id)
        summary = clean_text(self.summary, "route summary", maximum=200)
        if not callable(self.handler):
            raise TypeError("route handler must be callable")
        if not isinstance(self.tags, tuple):
            raise TypeError("route tags must be a tuple")
        tags = tuple(clean_text(value, "route tag", maximum=80) for value in self.tags)
        if (
            not isinstance(self.auth_required, bool)
            or not isinstance(self.expects_json_body, bool)
            or not isinstance(self.idempotency_required, bool)
        ):
            raise ConfigurationError("route flags must be boolean")
        if self.idempotency_required and method not in {"POST", "PUT", "PATCH", "DELETE"}:
            raise ConfigurationError(
                "idempotency keys may be required only for mutation methods"
            )
        if self.auth_required:
            if self.required_scope is None or self.resolve_target is None:
                raise ConfigurationError(
                    "protected routes require a scope and trusted target resolver"
                )
            required = scope(self.required_scope)
            if not callable(self.resolve_target):
                raise TypeError("target resolver must be callable")
        else:
            if self.required_scope is not None or self.resolve_target is not None:
                raise ConfigurationError(
                    "public routes cannot define authorization configuration"
                )
            if self.principal_rate_limit is not None:
                raise ConfigurationError(
                    "public routes cannot use principal rate limiting"
                )
            required = None
        if (
            not isinstance(self.max_body_bytes, int)
            or isinstance(self.max_body_bytes, bool)
            or not 1 <= self.max_body_bytes <= 16 * 1024 * 1024
        ):
            raise ConfigurationError("route body limit is invalid")
        if not isinstance(self.success_status, int) or isinstance(self.success_status, bool) or not 200 <= self.success_status <= 299:
            raise ConfigurationError("route success status is invalid")
        if self.request_schema is not None and not self.expects_json_body:
            raise ConfigurationError("request schema requires a JSON request body")
        if not isinstance(self.query_parameters, tuple):
            raise TypeError("route query parameters must be a tuple")
        query_names: set[str] = set()
        normalized_query_parameters: list[Mapping[str, Any]] = []
        for parameter in self.query_parameters:
            if not isinstance(parameter, Mapping):
                raise TypeError("route query parameter must be a mapping")
            item = dict(parameter)
            name = item.get("name")
            schema = item.get("schema")
            if (
                item.get("in") != "query"
                or not isinstance(name, str)
                or not name
                or name in query_names
                or not isinstance(schema, Mapping)
            ):
                raise ConfigurationError("route query parameter is invalid")
            query_names.add(name)
            normalized_query_parameters.append(item)
        for policy in (self.source_rate_limit, self.principal_rate_limit):
            if policy is not None and not isinstance(policy, RateLimitPolicy):
                raise TypeError("route rate limit must be RateLimitPolicy")
        object.__setattr__(self, "method", method)
        object.__setattr__(self, "operation_id", operation)
        object.__setattr__(self, "summary", summary)
        object.__setattr__(self, "tags", tags)
        object.__setattr__(self, "required_scope", required)
        object.__setattr__(
            self,
            "query_parameters",
            tuple(normalized_query_parameters),
        )

    @property
    def path_parameter_names(self) -> tuple[str, ...]:
        return compile_path(self.path)[1]


@dataclasses.dataclass(frozen=True, slots=True)
class _RegisteredRoute:
    route: Route
    regex: re.Pattern[str]
    parameter_names: tuple[str, ...]
    shape: str
