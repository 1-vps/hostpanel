from __future__ import annotations

import contextlib
import re
import secrets
import sqlite3

from .model import SCHEMA_VERSION, ValidationError, normalized_sql

SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS hp_api_security_state (
        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
        schema_version INTEGER NOT NULL,
        token_generation INTEGER NOT NULL CHECK (token_generation >= 0)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS hp_api_service_accounts (
        account_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
        allow_all_tenants INTEGER NOT NULL CHECK (allow_all_tenants IN (0, 1)),
        created_at INTEGER NOT NULL,
        expires_at INTEGER,
        CHECK (expires_at IS NULL OR expires_at > created_at)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS hp_api_account_scopes (
        account_id TEXT NOT NULL REFERENCES hp_api_service_accounts(account_id) ON DELETE CASCADE,
        scope TEXT NOT NULL,
        PRIMARY KEY (account_id, scope)
    ) WITHOUT ROWID
    """,
    """
    CREATE TABLE IF NOT EXISTS hp_api_account_tenants (
        account_id TEXT NOT NULL REFERENCES hp_api_service_accounts(account_id) ON DELETE CASCADE,
        tenant_id TEXT NOT NULL,
        PRIMARY KEY (account_id, tenant_id)
    ) WITHOUT ROWID
    """,
    """
    CREATE TABLE IF NOT EXISTS hp_api_account_cidrs (
        account_id TEXT NOT NULL REFERENCES hp_api_service_accounts(account_id) ON DELETE CASCADE,
        cidr TEXT NOT NULL,
        PRIMARY KEY (account_id, cidr)
    ) WITHOUT ROWID
    """,
    """
    CREATE TABLE IF NOT EXISTS hp_api_tokens (
        token_id TEXT PRIMARY KEY,
        account_id TEXT NOT NULL REFERENCES hp_api_service_accounts(account_id) ON DELETE CASCADE,
        secret_hash BLOB NOT NULL CHECK (length(secret_hash) = 32),
        hash_algorithm TEXT NOT NULL CHECK (hash_algorithm = 'sha256-v1'),
        generation INTEGER NOT NULL CHECK (generation >= 0),
        label TEXT NOT NULL,
        allow_all_tenants INTEGER NOT NULL CHECK (allow_all_tenants IN (0, 1)),
        created_at INTEGER NOT NULL,
        expires_at INTEGER NOT NULL,
        revoked_at INTEGER,
        last_used_at INTEGER,
        CHECK (expires_at > created_at),
        CHECK (revoked_at IS NULL OR revoked_at >= created_at),
        CHECK (last_used_at IS NULL OR last_used_at >= created_at)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS hp_api_tokens_account_idx
    ON hp_api_tokens(account_id, created_at, token_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS hp_api_token_scopes (
        token_id TEXT NOT NULL REFERENCES hp_api_tokens(token_id) ON DELETE CASCADE,
        scope TEXT NOT NULL,
        PRIMARY KEY (token_id, scope)
    ) WITHOUT ROWID
    """,
    """
    CREATE TABLE IF NOT EXISTS hp_api_token_tenants (
        token_id TEXT NOT NULL REFERENCES hp_api_tokens(token_id) ON DELETE CASCADE,
        tenant_id TEXT NOT NULL,
        PRIMARY KEY (token_id, tenant_id)
    ) WITHOUT ROWID
    """,
    """
    CREATE TABLE IF NOT EXISTS hp_api_token_cidrs (
        token_id TEXT NOT NULL REFERENCES hp_api_tokens(token_id) ON DELETE CASCADE,
        cidr TEXT NOT NULL,
        PRIMARY KEY (token_id, cidr)
    ) WITHOUT ROWID
    """,
    """
    CREATE TABLE IF NOT EXISTS hp_api_token_resources (
        token_id TEXT NOT NULL REFERENCES hp_api_tokens(token_id) ON DELETE CASCADE,
        resource_type TEXT NOT NULL,
        resource_id TEXT NOT NULL,
        PRIMARY KEY (token_id, resource_type, resource_id)
    ) WITHOUT ROWID
    """,
    """
    CREATE TABLE IF NOT EXISTS hp_api_audit_events (
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
    CREATE TRIGGER IF NOT EXISTS hp_api_audit_events_no_update
    BEFORE UPDATE ON hp_api_audit_events
    BEGIN
      SELECT RAISE(ABORT, 'HostPanel API audit events are append-only');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS hp_api_audit_events_no_delete
    BEFORE DELETE ON hp_api_audit_events
    BEGIN
      SELECT RAISE(ABORT, 'HostPanel API audit events are append-only');
    END
    """,
)


def _expected_schema_objects() -> dict[str, str]:
    expected: dict[str, str] = {}
    for statement in SCHEMA_STATEMENTS:
        match = re.match(
            r"\s*create\s+(?:table|index|trigger)\s+if\s+not\s+exists\s+([a-z0-9_]+)",
            statement,
            re.IGNORECASE,
        )
        if match is None:
            raise AssertionError("unreviewed API-token schema statement")
        expected[match.group(1)] = normalized_sql(statement)
    return expected


EXPECTED_SCHEMA_OBJECTS = _expected_schema_objects()


@contextlib.contextmanager
def atomic(connection: sqlite3.Connection):
    name = "hp_api_" + secrets.token_hex(8)
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
    trusted = connection.execute("PRAGMA trusted_schema").fetchone()
    if trusted is None or trusted[0] != 0:
        raise ValidationError("SQLite trusted_schema must be disabled")


def validate_schema_shape(connection: sqlite3.Connection) -> None:
    for name, expected_sql in EXPECTED_SCHEMA_OBJECTS.items():
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name = ? AND sql IS NOT NULL",
            (name,),
        ).fetchone()
        if row is None or normalized_sql(row[0]) != expected_sql:
            raise ValidationError(f"unexpected HostPanel API schema object: {name}")


def migrate(connection: sqlite3.Connection) -> None:
    with atomic(connection):
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
        row = connection.execute(
            "SELECT schema_version FROM hp_api_security_state WHERE singleton = 1"
        ).fetchone()
        if row is None:
            connection.execute(
                "INSERT INTO hp_api_security_state(singleton, schema_version, token_generation) VALUES(1, ?, 0)",
                (SCHEMA_VERSION,),
            )
        elif row[0] != SCHEMA_VERSION:
            raise ValidationError(
                f"unsupported HostPanel API token schema version: {row[0]}"
            )
        validate_schema_shape(connection)
