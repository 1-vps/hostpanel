from __future__ import annotations

import dataclasses
import pathlib
import sqlite3
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from hostpanel_api_audit import (  # noqa: E402
    ApiAuditStore,
    AuditEvent,
    ConflictError,
    REDACTED,
    RetentionCandidate,
    RetentionPolicy,
    StateError,
    ValidationError,
    redact_metadata,
)
from hostpanel_api_audit.hashing import genesis_hash  # noqa: E402
from hostpanel_api_audit.schema import SCHEMA_STATEMENTS  # noqa: E402

NOW = 2_000_000_000


def audit_store(*, policy: RetentionPolicy | None = None):
    connection = sqlite3.connect(":memory:")
    store = ApiAuditStore(connection, policy=policy)
    store.migrate()
    return connection, store


def append_sample(store: ApiAuditStore, *, tenant="tenant-a", actor="user-a", action="site.updated", at=NOW, **overrides):
    values = {
        "tenant_id": tenant,
        "actor_kind": "user",
        "actor_id": actor,
        "effective_principal_id": None,
        "action": action,
        "outcome": "succeeded",
        "target_kind": "site",
        "target_id": "site-a",
        "request_id": "request-0001",
        "source_ip": "2001:4860:4860::8888",
        "reason_code": "user_request",
        "metadata": {"safe": "visible"},
        "retention_class": "standard",
        "occurred_at": at,
    }
    values.update(overrides)
    return store.append_event(**values)


def recreate_event_triggers(connection: sqlite3.Connection) -> None:
    for statement in SCHEMA_STATEMENTS:
        if "CREATE TRIGGER" in statement:
            connection.execute(statement)


def replace_event(event: AuditEvent, **changes) -> AuditEvent:
    return dataclasses.replace(event, **changes)
