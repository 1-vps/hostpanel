from __future__ import annotations

import dataclasses
import ipaddress
import re
import time
from collections.abc import Callable, Mapping
from typing import Any, Protocol

REQUEST_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}\Z")
OPERATION_ID_RE = re.compile(r"[A-Za-z][A-Za-z0-9]{2,79}\Z")
SCOPE_RE = re.compile(r"[a-z][a-z0-9_.-]{0,63}:[a-z][a-z0-9_.-]{0,63}\Z")
IDENTIFIER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@+~-]{0,127}\Z")
HEADER_NAME_RE = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}\Z")

MAX_PATH_BYTES = 2048
MAX_QUERY_BYTES = 4096
MAX_HEADER_COUNT = 100
MAX_HEADER_VALUE_BYTES = 8192
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class HttpContractError(RuntimeError):
    """Base error for HostPanel API transport primitives."""


class ConfigurationError(HttpContractError):
    """The route table or transport configuration is invalid."""


class ApiProblem(HttpContractError):
    """A stable public API error."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        if not isinstance(status, int) or isinstance(status, bool) or not 400 <= status <= 599:
            raise ConfigurationError("API problem status is invalid")
        self.status = status
        self.code = identifier(code, "error code")
        self.message = clean_text(message, "error message", maximum=500)
        self.details = {} if details is None else dict(details)
        self.headers = {} if headers is None else dict(headers)


@dataclasses.dataclass(frozen=True, slots=True)
class Request:
    method: str
    raw_path: str
    query_string: str = ""
    headers: tuple[tuple[str, str], ...] = ()
    body: bytes = b""
    source_ip: str = "127.0.0.1"
    scheme: str = "https"
    host: str = "localhost"

    def __post_init__(self) -> None:
        method = normalize_method(self.method)
        if not isinstance(self.raw_path, str):
            raise ApiProblem(400, "invalid_path", "The request path is invalid.")
        if len(self.raw_path.encode("utf-8")) > MAX_PATH_BYTES:
            raise ApiProblem(414, "uri_too_long", "The request URI is too long.")
        if not isinstance(self.query_string, str) or len(
            self.query_string.encode("utf-8")
        ) > MAX_QUERY_BYTES:
            raise ApiProblem(414, "uri_too_long", "The request URI is too long.")
        if not isinstance(self.headers, tuple) or len(self.headers) > MAX_HEADER_COUNT:
            raise ApiProblem(431, "headers_too_large", "The request headers are too large.")
        normalized_headers: list[tuple[str, str]] = []
        for name, value in self.headers:
            normalized_headers.append((header_name(name), header_value(value)))
        if not isinstance(self.body, bytes):
            raise TypeError("request body must be bytes")
        source = source_ip(self.source_ip)
        if self.scheme not in {"http", "https"}:
            raise ApiProblem(400, "invalid_request", "The request is invalid.")
        host = clean_text(self.host, "host", maximum=255)
        if any(character in host for character in "\r\n/\\"):
            raise ApiProblem(400, "invalid_request", "The request is invalid.")
        object.__setattr__(self, "method", method)
        object.__setattr__(self, "headers", tuple(normalized_headers))
        object.__setattr__(self, "source_ip", source)
        object.__setattr__(self, "host", host)

    def header_values(self, name: str) -> tuple[str, ...]:
        target = header_name(name).lower()
        return tuple(value for key, value in self.headers if key.lower() == target)

    def single_header(self, name: str, *, required: bool = False) -> str | None:
        values = self.header_values(name)
        if len(values) > 1:
            raise ApiProblem(
                400,
                "duplicate_header",
                "A request header was supplied more than once.",
                details={"header": header_name(name).lower()},
            )
        if not values:
            if required:
                raise ApiProblem(
                    400,
                    "missing_header",
                    "A required request header is missing.",
                    details={"header": header_name(name).lower()},
                )
            return None
        return values[0]


@dataclasses.dataclass(frozen=True, slots=True)
class Response:
    status: int
    headers: tuple[tuple[str, str], ...]
    body: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.status, int) or isinstance(self.status, bool) or not 100 <= self.status <= 599:
            raise ConfigurationError("response status is invalid")
        if not isinstance(self.headers, tuple):
            raise TypeError("response headers must be a tuple")
        normalized: list[tuple[str, str]] = []
        for name, value in self.headers:
            normalized.append((header_name(name), header_value(value)))
        if not isinstance(self.body, bytes):
            raise TypeError("response body must be bytes")
        if len(self.body) > MAX_RESPONSE_BYTES:
            raise ConfigurationError("response body exceeds the transport limit")
        object.__setattr__(self, "headers", tuple(normalized))


@dataclasses.dataclass(frozen=True, slots=True)
class JsonResponse:
    payload: Mapping[str, Any]
    status: int = 200
    headers: Mapping[str, str] = dataclasses.field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.payload, Mapping):
            raise TypeError("JSON response payload must be a mapping")
        if (
            not isinstance(self.status, int)
            or isinstance(self.status, bool)
            or not 200 <= self.status <= 299
            or self.status in {204, 205}
        ):
            raise ConfigurationError("JSON response status is invalid")
        if not isinstance(self.headers, Mapping):
            raise TypeError("JSON response headers must be a mapping")


@dataclasses.dataclass(frozen=True, slots=True)
class PrincipalContext:
    principal_id: str
    principal_kind: str
    credential_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "principal_id", identifier(self.principal_id, "principal ID"))
        object.__setattr__(self, "principal_kind", identifier(self.principal_kind, "principal kind"))
        if self.credential_id is not None:
            object.__setattr__(self, "credential_id", identifier(self.credential_id, "credential ID"))


@dataclasses.dataclass(frozen=True, slots=True)
class AuthorizationTarget:
    tenant_id: str
    resource_type: str | None = None
    resource_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "tenant_id", identifier(self.tenant_id, "tenant ID"))
        if (self.resource_type is None) != (self.resource_id is None):
            raise ConfigurationError(
                "authorization target resource type and ID must be supplied together"
            )
        if self.resource_type is not None:
            object.__setattr__(
                self,
                "resource_type",
                identifier(self.resource_type, "resource type"),
            )
            object.__setattr__(
                self,
                "resource_id",
                identifier(self.resource_id, "resource ID"),
            )


@dataclasses.dataclass(frozen=True, slots=True)
class AccessGrant:
    principal: PrincipalContext
    target: AuthorizationTarget


TargetResolver = Callable[
    [PrincipalContext, Request, Mapping[str, str], Mapping[str, tuple[str, ...]]],
    AuthorizationTarget,
]


class AccessController(Protocol):
    def authorize(
        self,
        *,
        raw_token: str,
        required_scope: str,
        request: Request,
        path_parameters: Mapping[str, str],
        query_parameters: Mapping[str, tuple[str, ...]],
        resolve_target: TargetResolver,
        now: int,
    ) -> AccessGrant: ...


class RateLimiter(Protocol):
    def consume(
        self,
        *,
        identity: str,
        operation_id: str,
        policy: "RateLimitPolicy",
        now: int,
    ) -> "RateLimitDecision": ...


@dataclasses.dataclass(frozen=True, slots=True)
class RateLimitPolicy:
    limit: int
    window_seconds: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.limit, int)
            or isinstance(self.limit, bool)
            or not 1 <= self.limit <= 1_000_000
        ):
            raise ConfigurationError("rate limit is invalid")
        if (
            not isinstance(self.window_seconds, int)
            or isinstance(self.window_seconds, bool)
            or not 1 <= self.window_seconds <= 86_400
        ):
            raise ConfigurationError("rate-limit window is invalid")


@dataclasses.dataclass(frozen=True, slots=True)
class RateLimitDecision:
    allowed: bool
    limit: int
    remaining: int
    reset_at: int
    retry_after: int


@dataclasses.dataclass(frozen=True, slots=True)
class RequestContext:
    request: Request
    request_id: str
    path_parameters: Mapping[str, str]
    query_parameters: Mapping[str, tuple[str, ...]]
    principal: PrincipalContext | None
    target: AuthorizationTarget | None
    idempotency_key: str | None
    json_body: Mapping[str, Any] | None


def unix_time(value: int | None = None) -> int:
    timestamp = int(time.time()) if value is None else value
    if not isinstance(timestamp, int) or isinstance(timestamp, bool) or timestamp < 0:
        raise ConfigurationError("timestamp is invalid")
    return timestamp


def normalize_method(value: str) -> str:
    if not isinstance(value, str):
        raise ApiProblem(400, "invalid_method", "The request method is invalid.")
    method = value.upper()
    if re.fullmatch(r"[A-Z]{3,12}", method) is None:
        raise ApiProblem(400, "invalid_method", "The request method is invalid.")
    return method


def clean_text(value: str, label: str, *, maximum: int = 128) -> str:
    if not isinstance(value, str):
        raise ConfigurationError(f"{label} must be text")
    cleaned = value.strip()
    if not cleaned or len(cleaned) > maximum or any(
        ord(character) < 32 or ord(character) == 127 for character in cleaned
    ):
        raise ConfigurationError(f"{label} is invalid")
    return cleaned


def identifier(value: str, label: str) -> str:
    cleaned = clean_text(value, label)
    if IDENTIFIER_RE.fullmatch(cleaned) is None:
        raise ConfigurationError(f"{label} is invalid")
    return cleaned


def operation_id(value: str) -> str:
    if not isinstance(value, str) or OPERATION_ID_RE.fullmatch(value) is None:
        raise ConfigurationError("operation ID is invalid")
    return value


def scope(value: str) -> str:
    if not isinstance(value, str) or SCOPE_RE.fullmatch(value) is None:
        raise ConfigurationError("API scope is invalid")
    return value


def header_name(value: str) -> str:
    if not isinstance(value, str) or HEADER_NAME_RE.fullmatch(value) is None:
        raise ApiProblem(400, "invalid_header", "A request header is invalid.")
    return value


def header_value(value: str) -> str:
    if not isinstance(value, str):
        raise ApiProblem(400, "invalid_header", "A request header is invalid.")
    if (
        len(value.encode("utf-8")) > MAX_HEADER_VALUE_BYTES
        or "\r" in value
        or "\n" in value
        or "\x00" in value
    ):
        raise ApiProblem(400, "invalid_header", "A request header is invalid.")
    return value


def source_ip(value: str) -> str:
    try:
        return str(ipaddress.ip_address(value))
    except (TypeError, ValueError) as exc:
        raise ApiProblem(400, "invalid_source", "The request source is invalid.") from exc
