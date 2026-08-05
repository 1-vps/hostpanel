from __future__ import annotations

import base64
import dataclasses
import json
import re
import secrets
import time
from collections.abc import Iterable, Mapping
from typing import Any

SCHEMA_VERSION = 1
MAX_CAPABILITIES = 512
MAX_TENANTS = 1000
MAX_RESOURCES = 5000
MAX_AUDIT_PAGE = 1000
MAX_POLICY_MATCHES = 1000
MAX_IMPERSONATION_SECONDS = 60 * 60
MAX_REASON_LENGTH = 500
MAX_METADATA_BYTES = 4096

_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@/+~-]{0,127}\Z")
_CAPABILITY_RE = re.compile(r"[a-z][a-z0-9_.-]{0,63}:[a-z][a-z0-9_.-]{0,63}\Z")
ROLE_ID_RE = re.compile(r"role_[A-Za-z0-9_-]{22}\Z")
BINDING_ID_RE = re.compile(r"bind_[A-Za-z0-9_-]{22}\Z")
SESSION_ID_RE = re.compile(r"imp_[A-Za-z0-9_-]{22}\Z")
ACTOR_KINDS = frozenset({"system", "user", "operator", "service_account", "migration"})
PRINCIPAL_KINDS = frozenset({"admin", "operator", "reseller", "customer", "service_account"})
EFFECTS = frozenset({"allow", "deny"})


class RbacError(RuntimeError):
    """Base error for HostPanel capability policy."""


class ValidationError(RbacError):
    """Administrative input or persisted policy is invalid."""


class AuthorizationError(RbacError):
    """A policy decision denied the requested capability."""


class ConflictError(RbacError):
    """A unique policy object conflicts with an existing one."""


class StateError(RbacError):
    """A requested state transition is not currently valid."""


@dataclasses.dataclass(frozen=True, slots=True)
class AuditActor:
    kind: str
    actor_id: str


@dataclasses.dataclass(frozen=True, slots=True)
class Principal:
    principal_id: str
    principal_kind: str
    enabled: bool
    created_at: int
    expires_at: int | None


@dataclasses.dataclass(frozen=True, slots=True)
class Role:
    role_id: str
    name: str
    enabled: bool
    allows: tuple[str, ...]
    denies: tuple[str, ...]
    created_at: int
    updated_at: int


@dataclasses.dataclass(frozen=True, slots=True)
class RoleBinding:
    binding_id: str
    principal_id: str
    role_id: str
    tenant_ids: tuple[str, ...]
    allow_all_tenants: bool
    resources: tuple[tuple[str, str], ...]
    allow_all_resources: bool
    created_at: int
    expires_at: int | None
    revoked_at: int | None


@dataclasses.dataclass(frozen=True, slots=True)
class PolicyDecision:
    principal_id: str
    capability: str
    tenant_id: str
    resource_type: str | None
    resource_id: str | None
    allowed: bool
    reason: str
    allow_bindings: tuple[str, ...]
    deny_bindings: tuple[str, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class ImpersonationSession:
    session_id: str
    actor_principal_id: str
    target_principal_id: str
    tenant_id: str
    reason: str
    created_at: int
    expires_at: int
    ended_at: int | None
    end_reason: str | None


@dataclasses.dataclass(frozen=True, slots=True)
class ImpersonationDecision:
    session: ImpersonationSession
    allowed: bool
    reason: str
    actor_decision: PolicyDecision
    target_decision: PolicyDecision


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


def integer(value: int, label: str, *, minimum: int, maximum: int) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not minimum <= value <= maximum
    ):
        raise ValidationError(f"{label} is invalid")
    return value


def text(value: str, label: str, *, minimum: int = 1, maximum: int = 128) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{label} must be text")
    cleaned = value.strip()
    if (
        not minimum <= len(cleaned) <= maximum
        or any(ord(character) < 32 or ord(character) == 127 for character in cleaned)
    ):
        raise ValidationError(f"{label} is invalid")
    return cleaned


def identifier(value: str, label: str) -> str:
    cleaned = text(value, label)
    if _ID_RE.fullmatch(cleaned) is None:
        raise ValidationError(f"{label} is invalid")
    return cleaned


def capability(value: str) -> str:
    if not isinstance(value, str) or _CAPABILITY_RE.fullmatch(value) is None:
        raise ValidationError("capability is invalid")
    return value


def capabilities(values: Iterable[str]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise ValidationError("capabilities must be an iterable")
    try:
        normalized = tuple(sorted({capability(value) for value in values}))
    except TypeError as exc:
        raise ValidationError("capabilities must be iterable") from exc
    if len(normalized) > MAX_CAPABILITIES:
        raise ValidationError("too many capabilities")
    return normalized


def identifiers(values: Iterable[str], label: str, *, maximum: int) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise ValidationError(f"{label}s must be an iterable")
    try:
        normalized = tuple(sorted({identifier(value, label) for value in values}))
    except TypeError as exc:
        raise ValidationError(f"{label}s must be iterable") from exc
    if len(normalized) > maximum:
        raise ValidationError(f"too many {label}s")
    return normalized


def resources(values: Mapping[str, Iterable[str]] | None) -> tuple[tuple[str, str], ...]:
    if values is None:
        return ()
    if not isinstance(values, Mapping):
        raise ValidationError("resources must be a mapping")
    normalized: set[tuple[str, str]] = set()
    for raw_type, raw_ids in values.items():
        resource_type = identifier(raw_type, "resource type")
        resource_ids = identifiers(raw_ids, "resource ID", maximum=MAX_RESOURCES)
        if not resource_ids:
            raise ValidationError("resource restrictions cannot be empty")
        normalized.update((resource_type, resource_id) for resource_id in resource_ids)
        if len(normalized) > MAX_RESOURCES:
            raise ValidationError("too many resource restrictions")
    return tuple(sorted(normalized))


def audit_actor(value: AuditActor) -> AuditActor:
    if not isinstance(value, AuditActor) or value.kind not in ACTOR_KINDS:
        raise ValidationError("audit actor kind is invalid")
    return AuditActor(value.kind, identifier(value.actor_id, "audit actor ID"))


def encode_random(prefix: str) -> str:
    token = base64.urlsafe_b64encode(secrets.token_bytes(16)).rstrip(b"=")
    return prefix + token.decode("ascii")


def metadata_json(value: Mapping[str, Any]) -> str:
    if not isinstance(value, Mapping):
        raise ValidationError("audit metadata must be an object")
    if any(not isinstance(key, str) for key in value):
        raise ValidationError("audit metadata keys must be text")
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValidationError("audit metadata is not JSON serializable") from exc
    if len(encoded.encode("utf-8")) > MAX_METADATA_BYTES:
        raise ValidationError("audit metadata is too large")
    decoded = json.loads(encoded)
    if not isinstance(decoded, dict):
        raise ValidationError("audit metadata must be an object")
    return encoded


def decode_metadata(value: str) -> Mapping[str, Any]:
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError("persisted audit metadata is invalid") from exc
    if not isinstance(decoded, dict) or metadata_json(decoded) != value:
        raise ValidationError("persisted audit metadata is not canonical")
    return decoded


def normalized_sql(value: str) -> str:
    normalized = re.sub(r"\s+", " ", value.strip()).lower()
    return normalized.replace(" if not exists ", " ")
