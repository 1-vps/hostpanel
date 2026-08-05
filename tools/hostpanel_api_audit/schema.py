from __future__ import annotations

import contextlib
import re
import secrets
import sqlite3

from .hashing import genesis_hash
from .model import SCHEMA_VERSION, StateError, ValidationError

SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS hp_api_audit_state (
        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
        schema_version INTEGER NOT NULL,
        event_count INTEGER NOT NULL CHECK (event_count >= 0),
        last_sequence INTEGER NOT NULL CHECK (last_sequence >= 0),
        head_hash BLOB NOT NULL CHECK (length(head_hash) = 32),
        CHECK (event_count = last_sequence)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS hp_api_audit_events (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id TEXT NOT NULL UNIQUE,
        tenant_id TEXT NOT NULL,
        occurred_at INTEGER NOT NULL,
        actor_kind TEXT NOT NULL CHECK (actor_kind IN ('user', 'service_account', 'system', 'support')),
        actor_id TEXT NOT NULL,
        effective_principal_id TEXT,
        action TEXT NOT NULL,
        outcome TEXT NOT NULL CHECK (outcome IN ('succeeded', 'denied', 'failed')),
        target_kind TEXT,
        target_id TEXT,
        request_id TEXT,
        source_ip TEXT,
        reason_code TEXT,
        metadata_json TEXT NOT NULL,
        retention_class TEXT NOT NULL CHECK (retention_class IN ('standard', 'security', 'compliance', 'permanent')),
        expires_at INTEGER,
        previous_hash BLOB NOT NULL CHECK (length(previous_hash) = 32),
        event_hash BLOB NOT NULL UNIQUE CHECK (length(event_hash) = 32),
        CHECK ((target_kind IS NULL) = (target_id IS NULL)),
        CHECK (expires_at IS NULL OR expires_at > occurred_at)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS hp_api_audit_tenant_time_idx
    ON hp_api_audit_events(tenant_id, occurred_at, sequence)
    """,
    """
    CREATE INDEX IF NOT EXISTS hp_api_audit_actor_idx
    ON hp_api_audit_events(tenant_id, actor_id, occurred_at, sequence)
    """,
    """
    CREATE INDEX IF NOT EXISTS hp_api_audit_target_idx
    ON hp_api_audit_events(tenant_id, target_kind, target_id, occurred_at, sequence)
    """,
    """
    CREATE INDEX IF NOT EXISTS hp_api_audit_effective_idx
    ON hp_api_audit_events(tenant_id, effective_principal_id, occurred_at, sequence)
    """,
    """
    CREATE INDEX IF NOT EXISTS hp_api_audit_request_idx
    ON hp_api_audit_events(tenant_id, request_id, occurred_at, sequence)
    """,
    """
    CREATE INDEX IF NOT EXISTS hp_api_audit_action_idx
    ON hp_api_audit_events(tenant_id, action, occurred_at, sequence)
    """,
    """
    CREATE INDEX IF NOT EXISTS hp_api_audit_expiry_idx
    ON hp_api_audit_events(expires_at, sequence)
    WHERE expires_at IS NOT NULL
    """,
    """
    CREATE TRIGGER IF NOT EXISTS hp_api_audit_no_update
    BEFORE UPDATE ON hp_api_audit_events
    BEGIN
      SELECT RAISE(ABORT, 'HostPanel audit events are append-only');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS hp_api_audit_no_delete
    BEFORE DELETE ON hp_api_audit_events
    BEGIN
      SELECT RAISE(ABORT, 'HostPanel audit events are append-only');
    END
    """,
)


def normalized_sql(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).lower().replace(" if not exists ", " ")


def expected_schema_objects() -> dict[str, str]:
    result: dict[str, str] = {}
    for statement in SCHEMA_STATEMENTS:
        match = re.match(r"\s*create\s+(?:table|index|trigger)\s+if\s+not\s+exists\s+([a-z0-9_]+)", statement, re.I)
        if match is None:
            raise AssertionError("unreviewed audit schema statement")
        result[match.group(1)] = normalized_sql(statement)
    return result


EXPECTED_SCHEMA_OBJECTS = expected_schema_objects()


@contextlib.contextmanager
def atomic(connection: sqlite3.Connection):
    name = "hp_api_audit_" + secrets.token_hex(8)
    connection.execute(f"SAVEPOINT {name}")
    try:
        yield
    except BaseException:
        connection.execute(f"ROLLBACK TO {name}")
        connection.execute(f"RELEASE {name}")
        raise
    else:
        connection.execute(f"RELEASE {name}")


def initialize_connection(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA foreign_keys = ON")
    if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        raise ValidationError("SQLite foreign-key enforcement must be enabled")
    connection.execute("PRAGMA trusted_schema = OFF")
    row = connection.execute("PRAGMA trusted_schema").fetchone()
    if row is None or row[0] != 0:
        raise ValidationError("SQLite trusted_schema must be disabled")


def validate_schema_shape(connection: sqlite3.Connection) -> None:
    for name, expected in EXPECTED_SCHEMA_OBJECTS.items():
        row = connection.execute("SELECT sql FROM sqlite_master WHERE name = ? AND sql IS NOT NULL", (name,)).fetchone()
        if row is None or normalized_sql(row[0]) != expected:
            raise StateError(f"unexpected HostPanel audit schema object: {name}")


def migrate(connection: sqlite3.Connection) -> None:
    with atomic(connection):
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
        row = connection.execute("SELECT schema_version, event_count, last_sequence, head_hash FROM hp_api_audit_state WHERE singleton = 1").fetchone()
        if row is None:
            connection.execute(
                "INSERT INTO hp_api_audit_state(singleton, schema_version, event_count, last_sequence, head_hash) VALUES(1, ?, 0, 0, ?)",
                (SCHEMA_VERSION, genesis_hash()),
            )
        elif row[0] != SCHEMA_VERSION:
            raise ValidationError(f"unsupported HostPanel audit schema version: {row[0]}")
        validate_schema_shape(connection)
