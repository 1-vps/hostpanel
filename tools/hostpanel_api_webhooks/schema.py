from __future__ import annotations

import contextlib
import sqlite3
import uuid

from .model import StateError

_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS hp_api_webhook_destinations(
    destination_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    url TEXT NOT NULL,
    host TEXT NOT NULL,
    port INTEGER NOT NULL CHECK(port = 443),
    path TEXT NOT NULL,
    secret_ref TEXT NOT NULL,
    enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    disabled_at INTEGER,
    version INTEGER NOT NULL CHECK(version >= 1)
)
"""
_EVENT_SQL = """
CREATE TABLE IF NOT EXISTS hp_api_webhook_destination_events(
    destination_id TEXT NOT NULL REFERENCES hp_api_webhook_destinations(destination_id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    PRIMARY KEY(destination_id, event_type)
)
"""
_INDEX_SQL = "CREATE INDEX IF NOT EXISTS hp_api_webhook_destinations_tenant_idx ON hp_api_webhook_destinations(tenant_id, destination_id)"


def configure(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA trusted_schema = OFF")
    if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        raise StateError("SQLite foreign keys are required")
    trusted = connection.execute("PRAGMA trusted_schema").fetchone()
    if trusted is None or trusted[0] != 0:
        raise StateError("SQLite trusted_schema must be disabled")


@contextlib.contextmanager
def atomic(connection: sqlite3.Connection):
    name = "hp_webhooks_" + uuid.uuid4().hex
    connection.execute(f"SAVEPOINT {name}")
    try:
        yield
    except BaseException:
        connection.execute(f"ROLLBACK TO {name}")
        connection.execute(f"RELEASE {name}")
        raise
    else:
        connection.execute(f"RELEASE {name}")


def migrate(connection: sqlite3.Connection) -> None:
    configure(connection)
    with atomic(connection):
        connection.execute(_TABLE_SQL)
        connection.execute(_EVENT_SQL)
        connection.execute(_INDEX_SQL)
    verify_schema(connection)


def verify_schema(connection: sqlite3.Connection) -> None:
    configure(connection)
    expected_tables = {
        "hp_api_webhook_destinations": _normalize(_TABLE_SQL),
        "hp_api_webhook_destination_events": _normalize(_EVENT_SQL),
    }
    for name, expected in expected_tables.items():
        row = connection.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
        if row is None or _normalize(row[0]) != expected:
            raise StateError(f"webhook schema shape is invalid: {name}")
    row = connection.execute("SELECT sql FROM sqlite_master WHERE type='index' AND name='hp_api_webhook_destinations_tenant_idx'").fetchone()
    if row is None or _normalize(row[0]) != _normalize(_INDEX_SQL):
        raise StateError("webhook schema index shape is invalid")
    fk = connection.execute("PRAGMA foreign_key_list(hp_api_webhook_destination_events)").fetchall()
    if len(fk) != 1 or fk[0][2] != "hp_api_webhook_destinations" or fk[0][6].upper() != "CASCADE":
        raise StateError("webhook schema foreign key is invalid")


def _normalize(value: str) -> str:
    return " ".join(value.replace("\n", " ").split()).lower().replace(" if not exists ", " ")
