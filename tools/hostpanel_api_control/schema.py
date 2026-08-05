from __future__ import annotations

import contextlib
import re
import secrets
import sqlite3

from .model import SCHEMA_VERSION, ValidationError, normalized_sql

SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS hp_api_runtime_state (
        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
        schema_version INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS hp_api_idempotency (
        record_id TEXT PRIMARY KEY,
        principal_id TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        request_hash BLOB NOT NULL CHECK (length(request_hash) = 32),
        http_method TEXT NOT NULL,
        route TEXT NOT NULL,
        state TEXT NOT NULL CHECK (state IN ('pending', 'completed')),
        created_at INTEGER NOT NULL,
        expires_at INTEGER NOT NULL,
        completed_at INTEGER,
        response_status INTEGER CHECK (response_status IS NULL OR response_status BETWEEN 100 AND 599),
        response_headers_json TEXT,
        response_body BLOB,
        UNIQUE (principal_id, idempotency_key),
        CHECK (expires_at > created_at),
        CHECK (completed_at IS NULL OR completed_at >= created_at),
        CHECK (
          (state = 'pending' AND completed_at IS NULL AND response_status IS NULL
             AND response_headers_json IS NULL AND response_body IS NULL)
          OR
          (state = 'completed' AND completed_at IS NOT NULL AND response_status IS NOT NULL
             AND response_headers_json IS NOT NULL AND response_body IS NOT NULL)
        )
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS hp_api_idempotency_expiry_idx
    ON hp_api_idempotency(expires_at, record_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS hp_api_jobs (
        job_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        job_type TEXT NOT NULL,
        dedupe_key TEXT,
        payload_json TEXT NOT NULL,
        payload_hash BLOB NOT NULL CHECK (length(payload_hash) = 32),
        status TEXT NOT NULL CHECK (
          status IN ('pending', 'running', 'retry_wait', 'succeeded', 'failed', 'cancelled')
        ),
        result_json TEXT,
        error_text TEXT,
        priority INTEGER NOT NULL CHECK (priority BETWEEN 0 AND 100),
        created_at INTEGER NOT NULL,
        available_at INTEGER NOT NULL,
        started_at INTEGER,
        finished_at INTEGER,
        attempts INTEGER NOT NULL CHECK (attempts >= 0),
        max_attempts INTEGER NOT NULL CHECK (max_attempts BETWEEN 1 AND 100),
        cancel_requested INTEGER NOT NULL CHECK (cancel_requested IN (0, 1)),
        progress INTEGER NOT NULL CHECK (progress BETWEEN 0 AND 100),
        lease_owner TEXT,
        lease_expires_at INTEGER,
        CHECK (attempts <= max_attempts),
        CHECK (available_at >= created_at),
        CHECK (started_at IS NULL OR started_at >= created_at),
        CHECK (finished_at IS NULL OR finished_at >= created_at),
        CHECK (started_at IS NULL OR finished_at IS NULL OR finished_at >= started_at),
        CHECK (
          (status IN ('pending', 'running', 'retry_wait') AND finished_at IS NULL)
          OR
          (status IN ('succeeded', 'failed', 'cancelled') AND finished_at IS NOT NULL)
        ),
        CHECK (
          (status = 'running' AND started_at IS NOT NULL
             AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)
          OR
          (status != 'running' AND lease_owner IS NULL AND lease_expires_at IS NULL)
        ),
        CHECK (
          (status = 'succeeded' AND result_json IS NOT NULL
             AND error_text IS NULL AND progress = 100)
          OR
          (status != 'succeeded' AND result_json IS NULL)
        ),
        CHECK (status != 'failed' OR error_text IS NOT NULL)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS hp_api_jobs_claim_idx
    ON hp_api_jobs(status, available_at, priority DESC, created_at, job_id)
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS hp_api_jobs_active_dedupe_idx
    ON hp_api_jobs(tenant_id, job_type, dedupe_key)
    WHERE dedupe_key IS NOT NULL
      AND status IN ('pending', 'running', 'retry_wait')
    """,
    """
    CREATE TABLE IF NOT EXISTS hp_api_job_logs (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id TEXT NOT NULL REFERENCES hp_api_jobs(job_id) ON DELETE CASCADE,
        occurred_at INTEGER NOT NULL,
        level TEXT NOT NULL CHECK (level IN ('debug', 'info', 'warning', 'error')),
        message TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS hp_api_job_logs_job_idx
    ON hp_api_job_logs(job_id, sequence)
    """,
    """
    CREATE TABLE IF NOT EXISTS hp_api_outbox (
        event_id TEXT PRIMARY KEY,
        destination_id TEXT NOT NULL,
        event_type TEXT NOT NULL,
        dedupe_key TEXT,
        payload_json TEXT NOT NULL,
        payload_hash BLOB NOT NULL CHECK (length(payload_hash) = 32),
        status TEXT NOT NULL CHECK (
          status IN ('pending', 'delivering', 'retry_wait', 'delivered', 'failed')
        ),
        created_at INTEGER NOT NULL,
        available_at INTEGER NOT NULL,
        attempts INTEGER NOT NULL CHECK (attempts >= 0),
        max_attempts INTEGER NOT NULL CHECK (max_attempts BETWEEN 1 AND 100),
        lease_owner TEXT,
        lease_expires_at INTEGER,
        delivered_at INTEGER,
        last_error TEXT,
        CHECK (attempts <= max_attempts),
        CHECK (available_at >= created_at),
        CHECK (delivered_at IS NULL OR delivered_at >= created_at),
        CHECK (
          (status = 'delivering' AND attempts > 0
             AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)
          OR
          (status != 'delivering' AND lease_owner IS NULL AND lease_expires_at IS NULL)
        ),
        CHECK (
          (status = 'delivered' AND delivered_at IS NOT NULL AND last_error IS NULL)
          OR
          (status != 'delivered' AND delivered_at IS NULL)
        ),
        CHECK (status != 'failed' OR last_error IS NOT NULL)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS hp_api_outbox_claim_idx
    ON hp_api_outbox(status, available_at, created_at, event_id)
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS hp_api_outbox_dedupe_idx
    ON hp_api_outbox(destination_id, dedupe_key)
    WHERE dedupe_key IS NOT NULL
    """,
    """
    CREATE TABLE IF NOT EXISTS hp_api_webhook_receipts (
        receiver_id TEXT NOT NULL,
        event_id TEXT NOT NULL,
        received_at INTEGER NOT NULL,
        expires_at INTEGER NOT NULL,
        PRIMARY KEY (receiver_id, event_id),
        CHECK (expires_at > received_at)
    ) WITHOUT ROWID
    """,
    """
    CREATE INDEX IF NOT EXISTS hp_api_webhook_receipts_expiry_idx
    ON hp_api_webhook_receipts(expires_at, receiver_id, event_id)
    """,
)


def _expected_schema_objects() -> dict[str, str]:
    expected: dict[str, str] = {}
    for statement in SCHEMA_STATEMENTS:
        match = re.match(
            r"\s*create\s+(?:table|index|unique\s+index)\s+if\s+not\s+exists\s+([a-z0-9_]+)",
            statement,
            re.IGNORECASE,
        )
        if match is None:
            raise AssertionError("unreviewed API control-plane schema statement")
        expected[match.group(1)] = normalized_sql(statement)
    return expected


EXPECTED_SCHEMA_OBJECTS = _expected_schema_objects()


@contextlib.contextmanager
def atomic(connection: sqlite3.Connection):
    savepoint = "hp_api_control_" + secrets.token_hex(8)
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
            raise ValidationError(f"unexpected HostPanel API runtime schema object: {name}")


def migrate(connection: sqlite3.Connection) -> None:
    with atomic(connection):
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
        row = connection.execute(
            "SELECT schema_version FROM hp_api_runtime_state WHERE singleton = 1"
        ).fetchone()
        if row is None:
            connection.execute(
                "INSERT INTO hp_api_runtime_state(singleton, schema_version) VALUES(1, ?)",
                (SCHEMA_VERSION,),
            )
        elif row[0] != SCHEMA_VERSION:
            raise ValidationError(
                f"unsupported HostPanel API runtime schema version: {row[0]}"
            )
        validate_schema_shape(connection)
