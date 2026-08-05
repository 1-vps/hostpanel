from __future__ import annotations

import base64
import dataclasses
import hashlib
import json
import math
import re
import secrets
import time
from collections.abc import Mapping
from typing import Any

SCHEMA_VERSION = 1
MAX_IDEMPOTENCY_TTL = 24 * 60 * 60
MAX_RESPONSE_BODY = 1 << 20
MAX_JSON_BYTES = 64 << 10
MAX_JSON_DEPTH = 32
MAX_JSON_NODES = 10_000
MAX_HEADERS = 100
MAX_HEADER_BYTES = 16 << 10
MAX_JOB_PAGE = 1000
MAX_JOB_LOGS = 1000
MAX_LOG_BYTES = 4096
MAX_OUTBOX_PAGE = 1000
MAX_ATTEMPTS = 100
MAX_LEASE_SECONDS = 3600
MAX_WEBHOOK_TOLERANCE = 3600
MAX_RECEIPT_TTL = 7 * 24 * 60 * 60

_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@/+~-]{0,127}\Z")
_TYPE_RE = re.compile(r"[a-z][a-z0-9_.-]{0,127}\Z")
_METHOD_RE = re.compile(r"[A-Z]{3,16}\Z")
_ROUTE_RE = re.compile(r"/[A-Za-z0-9_./{}:~-]{0,511}\Z")
_KEY_RE = re.compile(r"[A-Za-z0-9._~:-]{8,128}\Z")
JOB_ID_RE = re.compile(r"job_[A-Za-z0-9_-]{22}\Z")
EVENT_ID_RE = re.compile(r"evt_[A-Za-z0-9_-]{22}\Z")
RECORD_ID_RE = re.compile(r"idem_[A-Za-z0-9_-]{22}\Z")
JOB_STATUSES = frozenset(
    {"pending", "running", "retry_wait", "succeeded", "failed", "cancelled"}
)
OUTBOX_STATUSES = frozenset(
    {"pending", "delivering", "retry_wait", "delivered", "failed"}
)


class ControlPlaneError(RuntimeError):
    """Base error for the API control-plane persistence layer."""


class ValidationError(ControlPlaneError):
    """Input or persisted state violates the reviewed contract."""


class ConflictError(ControlPlaneError):
    """A unique or idempotent key was reused for a different operation."""


class StateError(ControlPlaneError):
    """An operation is invalid for the current durable state."""


class AuthenticationError(ControlPlaneError):
    """A signed webhook cannot be authenticated."""


class ReplayError(AuthenticationError):
    """A valid webhook event has already been consumed."""


@dataclasses.dataclass(frozen=True, slots=True)
class IdempotencyDecision:
    record_id: str
    created: bool
    state: str
    response_status: int | None = None
    response_headers: Mapping[str, str] | None = None
    response_body: bytes | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class Job:
    job_id: str
    tenant_id: str
    job_type: str
    dedupe_key: str | None
    status: str
    payload: Mapping[str, Any]
    result: Mapping[str, Any] | None
    error: str | None
    priority: int
    created_at: int
    available_at: int
    started_at: int | None
    finished_at: int | None
    attempts: int
    max_attempts: int
    cancel_requested: bool
    progress: int
    lease_owner: str | None
    lease_expires_at: int | None


@dataclasses.dataclass(frozen=True, slots=True)
class JobCreation:
    job: Job
    created: bool


@dataclasses.dataclass(frozen=True, slots=True)
class JobLog:
    sequence: int
    job_id: str
    occurred_at: int
    level: str
    message: str


@dataclasses.dataclass(frozen=True, slots=True)
class OutboxEvent:
    event_id: str
    destination_id: str
    event_type: str
    dedupe_key: str | None
    payload: Mapping[str, Any]
    status: str
    created_at: int
    available_at: int
    attempts: int
    max_attempts: int
    lease_owner: str | None
    lease_expires_at: int | None
    delivered_at: int | None
    last_error: str | None


@dataclasses.dataclass(frozen=True, slots=True)
class OutboxCreation:
    event: OutboxEvent
    created: bool


def now(value: int | None) -> int:
    timestamp = int(time.time()) if value is None else value
    if not isinstance(timestamp, int) or isinstance(timestamp, bool) or timestamp < 0:
        raise ValidationError("timestamp must be a non-negative integer")
    return timestamp


def integer(value: int, label: str, *, minimum: int, maximum: int) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not minimum <= value <= maximum
    ):
        raise ValidationError(f"{label} is invalid")
    return value


def text(value: str, label: str, *, maximum: int = 128) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{label} must be text")
    cleaned = value.strip()
    if (
        not cleaned
        or len(cleaned) > maximum
        or any(ord(ch) < 32 or ord(ch) == 127 for ch in cleaned)
    ):
        raise ValidationError(f"{label} is invalid")
    return cleaned


def identifier(value: str, label: str) -> str:
    cleaned = text(value, label)
    if _ID_RE.fullmatch(cleaned) is None:
        raise ValidationError(f"{label} is invalid")
    return cleaned


def kind(value: str, label: str) -> str:
    if not isinstance(value, str) or _TYPE_RE.fullmatch(value) is None:
        raise ValidationError(f"{label} is invalid")
    return value


def method(value: str) -> str:
    if not isinstance(value, str) or _METHOD_RE.fullmatch(value) is None:
        raise ValidationError("HTTP method is invalid")
    return value


def route(value: str) -> str:
    if not isinstance(value, str) or _ROUTE_RE.fullmatch(value) is None:
        raise ValidationError("route is invalid")
    return value


def idempotency_key(value: str) -> str:
    if not isinstance(value, str) or _KEY_RE.fullmatch(value) is None:
        raise ValidationError("idempotency key is invalid")
    return value


def optional_key(value: str | None, label: str = "dedupe key") -> str | None:
    if value is None:
        return None
    cleaned = text(value, label, maximum=128)
    if _KEY_RE.fullmatch(cleaned) is None:
        raise ValidationError(f"{label} is invalid")
    return cleaned


def encode_random(prefix: str) -> str:
    token = base64.urlsafe_b64encode(secrets.token_bytes(16)).rstrip(b"=")
    return prefix + token.decode("ascii")


def _validate_json_tree(value: Any, label: str) -> None:
    remaining = [MAX_JSON_NODES]

    def visit(node: Any, depth: int) -> None:
        if depth > MAX_JSON_DEPTH:
            raise ValidationError(f"{label} is too deeply nested")
        remaining[0] -= 1
        if remaining[0] < 0:
            raise ValidationError(f"{label} contains too many values")
        if node is None or isinstance(node, (str, bool)):
            return
        if isinstance(node, int) and not isinstance(node, bool):
            if not -(2**63) <= node <= 2**63 - 1:
                raise ValidationError(f"{label} contains an out-of-range integer")
            return
        if isinstance(node, float):
            if not math.isfinite(node):
                raise ValidationError(f"{label} contains a non-finite number")
            return
        if isinstance(node, Mapping):
            for key, child in node.items():
                if not isinstance(key, str) or len(key) > 256:
                    raise ValidationError(f"{label} contains an invalid object key")
                visit(child, depth + 1)
            return
        if isinstance(node, (list, tuple)):
            for child in node:
                visit(child, depth + 1)
            return
        raise ValidationError(f"{label} is not JSON serializable")

    visit(value, 0)


def canonical_json(value: Mapping[str, Any] | None, label: str) -> tuple[str, bytes]:
    if value is None:
        value = {}
    if not isinstance(value, Mapping):
        raise ValidationError(f"{label} must be an object")
    _validate_json_tree(value, label)
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{label} is not JSON serializable") from exc
    raw = encoded.encode("utf-8")
    if len(raw) > MAX_JSON_BYTES:
        raise ValidationError(f"{label} is too large")
    decoded = json.loads(encoded)
    if not isinstance(decoded, dict):
        raise ValidationError(f"{label} must be an object")
    return encoded, hashlib.sha256(raw).digest()


def decode_json(value: str | None) -> Mapping[str, Any] | None:
    if value is None:
        return None
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError("persisted JSON object is invalid") from exc
    if not isinstance(decoded, dict):
        raise ValidationError("persisted JSON object has an invalid shape")
    canonical, _digest = canonical_json(decoded, "persisted JSON object")
    if canonical != value:
        raise ValidationError("persisted JSON object is not canonical")
    return decoded


def response_headers(value: Mapping[str, str] | None) -> str:
    if value is None:
        value = {}
    if not isinstance(value, Mapping) or len(value) > MAX_HEADERS:
        raise ValidationError("response headers are invalid")
    normalized: dict[str, str] = {}
    for raw_name, raw_value in value.items():
        name = text(raw_name, "header name", maximum=128).lower()
        if re.fullmatch(r"[a-z0-9!#$%&'*+.^_`|~-]+", name) is None:
            raise ValidationError("header name is invalid")
        header_value = text(raw_value, "header value", maximum=2048)
        if "\r" in header_value or "\n" in header_value:
            raise ValidationError("header value is invalid")
        normalized[name] = header_value
    encoded = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_HEADER_BYTES:
        raise ValidationError("response headers are too large")
    return encoded


def decode_response_headers(value: str | None) -> Mapping[str, str] | None:
    if value is None:
        return None
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError("persisted response headers are invalid") from exc
    if not isinstance(decoded, dict) or any(
        not isinstance(key, str) or not isinstance(item, str)
        for key, item in decoded.items()
    ):
        raise ValidationError("persisted response headers are invalid")
    if response_headers(decoded) != value:
        raise ValidationError("persisted response headers are not canonical")
    return decoded


def request_fingerprint(http_method: str, request_route: str, body: bytes) -> bytes:
    verb = method(http_method)
    path = route(request_route)
    if not isinstance(body, bytes) or len(body) > MAX_RESPONSE_BODY:
        raise ValidationError("request body is invalid")
    return hashlib.sha256(
        b"hostpanel-idempotency-v1\0"
        + verb.encode("ascii")
        + b"\0"
        + path.encode("utf-8")
        + b"\0"
        + hashlib.sha256(body).digest()
    ).digest()


def normalized_sql(value: str) -> str:
    normalized = re.sub(r"\s+", " ", value.strip()).lower()
    return normalized.replace(" if not exists ", " ")
