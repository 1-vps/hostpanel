from __future__ import annotations

import contextlib
import re
import secrets
import sqlite3

from .model import SCHEMA_VERSION, ValidationError, normalized_sql

SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS hp_api_rbac_state (
        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
        schema_version INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS hp_api_principals (
        principal_id TEXT PRIMARY KEY,
        principal_kind TEXT NOT NULL CHECK (
          principal_kind IN ('admin', 'operator', 'reseller', 'customer', 'service_account')
        ),
        enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
        created_at INTEGER NOT NULL,
        expires_at INTEGER,
        CHECK (expires_at IS NULL OR expires_at > created_at)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS hp_api_roles (
        role_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        CHECK (updated_at >= created_at)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS hp_api_role_capabilities (
        role_id TEXT NOT NULL REFERENCES hp_api_roles(role_id) ON DELETE CASCADE,
        capability TEXT NOT NULL,
        effect TEXT NOT NULL CHECK (effect IN ('allow', 'deny')),
        PRIMARY KEY (role_id, capability)
    ) WITHOUT ROWID
    """,
    """
    CREATE TABLE IF NOT EXISTS hp_api_role_bindings (
        binding_id TEXT PRIMARY KEY,
        principal_id TEXT NOT NULL REFERENCES hp_api_principals(principal_id) ON DELETE CASCADE,
        role_id TEXT NOT NULL REFERENCES hp_api_roles(role_id) ON DELETE CASCADE,
        allow_all_tenants INTEGER NOT NULL CHECK (allow_all_tenants IN (0, 1)),
        allow_all_resources INTEGER NOT NULL CHECK (allow_all_resources IN (0, 1)),
        created_at INTEGER NOT NULL,
        expires_at INTEGER,
        revoked_at INTEGER,
        CHECK (expires_at IS NULL OR expires_at > created_at),
        CHECK (revoked_at IS NULL OR revoked_at >= created_at)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS hp_api_role_bindings_principal_idx
    ON hp_api_role_bindings(principal_id, created_at, binding_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS hp_api_binding_tenants (
        binding_id TEXT NOT NULL REFERENCES hp_api_role_bindings(binding_id) ON DELETE CASCADE,
        tenant_id TEXT NOT NULL,
        PRIMARY KEY (binding_id, tenant_id)
    ) WITHOUT ROWID
    """,
    """
    CREATE TABLE IF NOT EXISTS hp_api_binding_resources (
        binding_id TEXT NOT NULL REFERENCES hp_api_role_bindings(binding_id) ON DELETE CASCADE,
        resource_type TEXT NOT NULL,
        resource_id TEXT NOT NULL,
        PRIMARY KEY (binding_id, resource_type, resource_id)
    ) WITHOUT ROWID
    """,
    """
    CREATE TABLE IF NOT EXISTS hp_api_impersonation_sessions (
        session_id TEXT PRIMARY KEY,
        actor_principal_id TEXT NOT NULL REFERENCES hp_api_principals(principal_id) ON DELETE CASCADE,
        target_principal_id TEXT NOT NULL REFERENCES hp_api_principals(principal_id) ON DELETE CASCADE,
        tenant_id TEXT NOT NULL,
        reason TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        expires_at INTEGER NOT NULL,
        ended_at INTEGER,
        end_reason TEXT,
        CHECK (actor_principal_id != target_principal_id),
        CHECK (expires_at > created_at),
        CHECK (ended_at IS NULL OR ended_at >= created_at),
        CHECK (
          (ended_at IS NULL AND end_reason IS NULL)
          OR
          (ended_at IS NOT NULL AND end_reason IS NOT NULL)
        )
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS hp_api_impersonation_actor_active_idx
    ON hp_api_impersonation_sessions(actor_principal_id)
    WHERE ended_at IS NULL
    """,
    """
    CREATE TABLE IF NOT EXISTS hp_api_rbac_audit_events (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id TEXT NOT NULL UNIQUE,
        occurred_at INTEGER NOT NULL,
        actor_kind TEXT NOT NULL,
        actor_id TEXT NOT NULL,
        action TEXT NOT NULL,
        target_kind TEXT NOT NULL,
        target_id TEXT NOT NULL,
        metadata_json TEXT NOT NULL
    )
    """,
    """
    CREATE TRIGGER IF NOT EXISTS hp_api_rbac_audit_no_update
    BEFORE UPDATE ON hp_api_rbac_audit_events
    BEGIN
      SELECT RAISE(ABORT, 'HostPanel RBAC audit events are append-only');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS hp_api_rbac_audit_no_delete
    BEFORE DELETE ON hp_api_rbac_audit_events
    BEGIN
      SELECT RAISE(ABORT, 'HostPanel RBAC audit events are append-only');
    END
    """,
)


def _expected_schema_objects() -> dict[str, str]:
    expected: dict[str, str] = {}
    for statement in SCHEMA_STATEMENTS:
        match = re.match(
            r"\s*create\s+(?:table|index|unique\s+index|trigger)\s+if\s+not\s+exists\s+([a-z0-9_]+)",
            statement,
            re.IGNORECASE,
        )
        if match is None:
            raise AssertionError("unreviewed RBAC schema statement")
        expected[match.group(1)] = normalized_sql(statement)
    return expected


EXPECTED_SCHEMA_OBJECTS = _expected_schema_objects()


@contextlib.contextmanager
def atomic(connection: sqlite3.Connection):
    savepoint = "hp_api_rbac_" + secrets.token_hex(8)
    connection.execute(f"SAVEPOINT {savepoint}")
    try:
        yield
    except BaseException:
        connection.execute(f"ROLLBACK TO {savepoint}")
        connection.execute(f"RELEASE {savepoint}")
        raise
    else:
        connection.execute(f"RELEASE {savepoint}")


def initialize_connection(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA foreign_keys = ON")
    if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        raise ValidationError("SQLite foreign-key enforcement must be enabled")
    connection.execute("PRAGMA trusted_schema = OFF")
    trusted = connection.execute("PRAGMA trusted_schema").fetchone()
    if trusted is None or trusted[0] != 0:
        raise ValidationError("SQLite trusted_schema must be disabled")


def validate_schema_shape(connection: sqlite3.Connection) -> None:
    for name, expected in EXPECTED_SCHEMA_OBJECTS.items():
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name = ? AND sql IS NOT NULL",
            (name,),
        ).fetchone()
        if row is None or normalized_sql(row[0]) != expected:
            raise ValidationError(f"unexpected HostPanel RBAC schema object: {name}")


def migrate(connection: sqlite3.Connection) -> None:
    with atomic(connection):
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
        row = connection.execute(
            "SELECT schema_version FROM hp_api_rbac_state WHERE singleton = 1"
        ).fetchone()
        if row is None:
            connection.execute(
                "INSERT INTO hp_api_rbac_state(singleton, schema_version) VALUES(1, ?)",
                (SCHEMA_VERSION,),
            )
        elif row[0] != SCHEMA_VERSION:
            raise ValidationError(
                f"unsupported HostPanel RBAC schema version: {row[0]}"
            )
        validate_schema_shape(connection)
