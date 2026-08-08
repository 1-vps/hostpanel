from __future__ import annotations

import dataclasses
import hashlib
import io
import json
import sqlite3
import sys
import types
from collections.abc import Mapping
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "tools", ROOT):
    value = str(path)
    if value not in sys.path:
        sys.path.insert(0, value)

from hostpanel_api_http import RateLimitDecision


@dataclasses.dataclass(frozen=True, slots=True)
class Actor:
    kind: str
    actor_id: str


@dataclasses.dataclass(frozen=True, slots=True)
class AuditActor:
    kind: str
    actor_id: str


@dataclasses.dataclass(frozen=True, slots=True)
class Identity:
    account_id: str
    token_id: str
    scopes: frozenset[str] = frozenset()
    tenant_ids: frozenset[str] = frozenset()
    allow_all_tenants: bool = False


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
class RbacPrincipal:
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
class Binding:
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
    allow_bindings: tuple[str, ...] = ()
    deny_bindings: tuple[str, ...] = ()


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
class Destination:
    destination_id: str
    tenant_id: str
    url: str
    secret_reference: str
    event_types: tuple[str, ...]
    enabled: bool
    version: int
    created_at: int
    updated_at: int


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
    previous_hash: str = "00" * 32
    event_hash: str = "11" * 32


def install_domain_stubs() -> None:
    token_module = types.ModuleType("hostpanel_api_tokens")
    token_module.Actor = Actor
    token_module.Principal = Identity
    sys.modules["hostpanel_api_tokens"] = token_module
    rbac_module = types.ModuleType("hostpanel_api_rbac")
    rbac_module.AuditActor = AuditActor
    sys.modules["hostpanel_api_rbac"] = rbac_module
