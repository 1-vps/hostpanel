from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import Any

SCHEMA_VERSION = 1
MAX_METADATA_BYTES = 32 << 10
MAX_METADATA_DEPTH = 16
MAX_METADATA_NODES = 2_000
MAX_PAGE_SIZE = 1_000
MAX_EXPORT_EVENTS = 10_000
MAX_RETENTION_SECONDS = 10 * 366 * 24 * 60 * 60
GENESIS_HASH_DOMAIN = b"hostpanel-audit-genesis-v1\0"
EVENT_HASH_DOMAIN = b"hostpanel-audit-event-v1\0"


class AuditError(RuntimeError):
    """Base error for the HostPanel audit foundation."""


class ValidationError(AuditError):
    """Input or persisted audit state violates the reviewed contract."""


class StateError(AuditError):
    """The durable audit state is missing, inconsistent, or tampered."""


class ConflictError(AuditError):
    """An immutable audit identifier or optimistic expectation conflicted."""


@dataclasses.dataclass(frozen=True, slots=True)
class RetentionPolicy:
    standard_seconds: int = 90 * 24 * 60 * 60
    security_seconds: int = 365 * 24 * 60 * 60
    compliance_seconds: int = 7 * 365 * 24 * 60 * 60
    permanent_seconds: None = None


@dataclasses.dataclass(frozen=True, slots=True)
class AuditEvent:
    sequence: int
    event_id: str
    tenant_id: str
    occurred_at: int
    actor_kind: str
    actor_id: str
    effective_principal_id: str | None
    action: str
    outcome: str
    target_kind: str | None
    target_id: str | None
    request_id: str | None
    source_ip: str | None
    reason_code: str | None
    metadata: Mapping[str, Any]
    retention_class: str
    expires_at: int | None
    previous_hash: bytes
    event_hash: bytes


@dataclasses.dataclass(frozen=True, slots=True)
class RetentionCandidate:
    sequence: int
    event_id: str
    tenant_id: str
    occurred_at: int
    expires_at: int
    event_hash: bytes


@dataclasses.dataclass(frozen=True, slots=True)
class ChainVerification:
    event_count: int
    head_hash: bytes
    last_sequence: int
