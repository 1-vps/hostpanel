from __future__ import annotations

import base64
import dataclasses
import hashlib
import ipaddress
import re
import secrets
import time
from collections.abc import Iterable, Mapping
from typing import Any

SCHEMA_VERSION = 1
MAX_TOKEN_TTL_SECONDS = 366 * 24 * 60 * 60
MAX_AUDIT_PAGE = 1000
MAX_TOKEN_PAGE = 1000
MAX_SCOPES = 128
MAX_TENANTS = 1000
MAX_SOURCE_CIDRS = 64
MAX_RESOURCE_GRANTS = 5000
TOKEN_PREFIX = "hpv1"
HASH_ALGORITHM = "sha256-v1"
_SCOPE_RE = re.compile(r"[a-z][a-z0-9_.-]{0,63}:[a-z][a-z0-9_.-]{0,63}\Z")
_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@/+~-]{0,127}\Z")
ACCOUNT_ID_RE = re.compile(r"sa_[A-Za-z0-9_-]{22}\Z")
TOKEN_ID_RE = re.compile(r"tok_[A-Za-z0-9_-]{22}\Z")
ACTOR_KINDS = frozenset({"system", "user", "operator", "service_account", "migration"})
DUMMY_HASH = hashlib.sha256(b"hostpanel-api-token-v1\0invalid").digest()


class ApiTokenError(RuntimeError):
    """Base class for API-token failures."""


class ValidationError(ApiTokenError):
    """An administrative request violates the token policy contract."""


class AuthenticationError(ApiTokenError):
    """A credential is absent, malformed, or no longer valid."""


class AuthorizationError(ApiTokenError):
    """A valid credential does not grant the requested operation."""


@dataclasses.dataclass(frozen=True, slots=True)
class Actor:
    kind: str
    actor_id: str


@dataclasses.dataclass(frozen=True, slots=True)
class ServiceAccount:
    account_id: str
    name: str
    enabled: bool
    scopes: tuple[str, ...]
    tenant_ids: tuple[str, ...]
    allow_all_tenants: bool
    source_cidrs: tuple[str, ...]
    created_at: int
    expires_at: int | None


@dataclasses.dataclass(frozen=True, slots=True)
class TokenMetadata:
    token_id: str
    account_id: str
    label: str
    scopes: tuple[str, ...]
    tenant_ids: tuple[str, ...]
    allow_all_tenants: bool
    source_cidrs: tuple[str, ...]
    resources: tuple[tuple[str, str], ...]
    created_at: int
    expires_at: int
    revoked_at: int | None
    last_used_at: int | None
    generation: int


@dataclasses.dataclass(frozen=True, slots=True)
class IssuedToken:
    token: str
    metadata: TokenMetadata


@dataclasses.dataclass(frozen=True, slots=True)
class Principal:
    account_id: str
    token_id: str
    scopes: frozenset[str]
    tenant_ids: frozenset[str]
    allow_all_tenants: bool


@dataclasses.dataclass(frozen=True, slots=True)
class AuditEvent:
    sequence: int
    event_id: str
    occurred_at: int
    actor_kind: str
    actor_id: str
    action: str
    target_kind: str
    target_id: str
    metadata: Mapping[str, Any]


def now(value: int | None) -> int:
    timestamp = int(time.time()) if value is None else value
    if not isinstance(timestamp, int) or isinstance(timestamp, bool) or timestamp < 0:
        raise ValidationError("timestamp must be a non-negative integer")
    return timestamp


def clean_text(value: str, label: str, *, maximum: int = 128) -> str:
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
    cleaned = clean_text(value, label)
    if _ID_RE.fullmatch(cleaned) is None:
        raise ValidationError(f"{label} is invalid")
    return cleaned


def scope(value: str) -> str:
    if not isinstance(value, str) or _SCOPE_RE.fullmatch(value) is None:
        raise ValidationError("scope is invalid")
    return value


def scopes(values: Iterable[str], *, required: bool = True) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise ValidationError("scopes must be an iterable of scope strings")
    try:
        normalized = tuple(sorted({scope(value) for value in values}))
    except TypeError as exc:
        raise ValidationError("scopes must be iterable") from exc
    if required and not normalized:
        raise ValidationError("at least one scope is required")
    if len(normalized) > MAX_SCOPES:
        raise ValidationError("too many scopes")
    return normalized


def identifiers(
    values: Iterable[str],
    label: str,
    *,
    maximum: int = MAX_TENANTS,
) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise ValidationError(f"{label}s must be an iterable of identifiers")
    try:
        normalized = tuple(sorted({identifier(value, label) for value in values}))
    except TypeError as exc:
        raise ValidationError(f"{label}s must be iterable") from exc
    if len(normalized) > maximum:
        raise ValidationError(f"too many {label}s")
    return normalized


def cidrs(values: Iterable[str]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise ValidationError("source CIDRs must be an iterable of networks")
    normalized: set[str] = set()
    try:
        for value in values:
            if not isinstance(value, str):
                raise ValidationError("source CIDR must be text")
            try:
                network = ipaddress.ip_network(value.strip(), strict=True)
            except ValueError as exc:
                raise ValidationError("source CIDR is invalid") from exc
            normalized.add(network.compressed)
    except TypeError as exc:
        raise ValidationError("source CIDRs must be iterable") from exc
    if len(normalized) > MAX_SOURCE_CIDRS:
        raise ValidationError("too many source CIDRs")
    return tuple(sorted(normalized))


def resources(values: Mapping[str, Iterable[str]] | None) -> tuple[tuple[str, str], ...]:
    if values is None:
        return ()
    if not isinstance(values, Mapping):
        raise ValidationError("resources must be a mapping")
    normalized: set[tuple[str, str]] = set()
    for resource_type, values_for_type in values.items():
        kind = identifier(resource_type, "resource type")
        resource_ids = identifiers(
            values_for_type,
            "resource ID",
            maximum=MAX_RESOURCE_GRANTS,
        )
        if not resource_ids:
            raise ValidationError("resource restrictions cannot be empty")
        normalized.update((kind, resource_id) for resource_id in resource_ids)
        if len(normalized) > MAX_RESOURCE_GRANTS:
            raise ValidationError("too many resource restrictions")
    return tuple(sorted(normalized))


def actor(value: Actor) -> Actor:
    if not isinstance(value, Actor) or value.kind not in ACTOR_KINDS:
        raise ValidationError("actor kind is invalid")
    return Actor(value.kind, identifier(value.actor_id, "actor ID"))


def expiry(created_at: int, expires_at: int) -> int:
    expiry_value = now(expires_at)
    ttl = expiry_value - created_at
    if ttl <= 0 or ttl > MAX_TOKEN_TTL_SECONDS:
        raise ValidationError(
            "token expiry must be in the future and no more than 366 days away"
        )
    return expiry_value


def encode_random(size: int) -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(size)).rstrip(b"=").decode("ascii")


def decode_secret(encoded: str) -> bytes:
    if (
        not isinstance(encoded, str)
        or len(encoded) != 43
        or re.fullmatch(r"[A-Za-z0-9_-]{43}", encoded) is None
    ):
        raise AuthenticationError("invalid API token")
    try:
        raw = base64.urlsafe_b64decode(encoded + "=")
    except (ValueError, TypeError) as exc:
        raise AuthenticationError("invalid API token") from exc
    canonical = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    if len(raw) != 32 or canonical != encoded:
        raise AuthenticationError("invalid API token")
    return raw


def secret_hash(secret: bytes) -> bytes:
    return hashlib.sha256(b"hostpanel-api-token-v1\0" + secret).digest()


def normalized_sql(value: str) -> str:
    normalized = re.sub(r"\s+", " ", value.strip()).lower()
    return normalized.replace(" if not exists ", " ")
