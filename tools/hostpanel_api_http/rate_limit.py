from __future__ import annotations

import contextlib
import hmac
import hashlib
import re
import secrets
import sqlite3

from .model import (
    ConfigurationError,
    RateLimitDecision,
    RateLimitPolicy,
    clean_text,
    operation_id as normalize_operation_id,
    unix_time,
)

SCHEMA_VERSION = 1
_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS hp_api_http_rate_limit_state (
        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
        schema_version INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS hp_api_http_rate_limits (
        bucket_hash BLOB NOT NULL CHECK (length(bucket_hash) = 32),
        operation_id TEXT NOT NULL,
        window_start INTEGER NOT NULL CHECK (window_start >= 0),
        request_count INTEGER NOT NULL CHECK (request_count >= 1),
        PRIMARY KEY (bucket_hash, operation_id)
    ) WITHOUT ROWID
    """,
    """
    CREATE INDEX IF NOT EXISTS hp_api_http_rate_limits_window_idx
    ON hp_api_http_rate_limits(window_start)
    """,
)


def _normalized_sql(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).lower().replace(" if not exists ", " ")


def _schema_names() -> dict[str, str]:
    result: dict[str, str] = {}
    for statement in _SCHEMA_STATEMENTS:
        match = re.match(
            r"\s*create\s+(?:table|index)\s+if\s+not\s+exists\s+([a-z0-9_]+)",
            statement,
            re.IGNORECASE,
        )
        if match is None:
            raise AssertionError("unreviewed HTTP rate-limit schema statement")
        result[match.group(1)] = _normalized_sql(statement)
    return result


_EXPECTED_SCHEMA = _schema_names()


@contextlib.contextmanager
def _atomic(connection: sqlite3.Connection):
    savepoint = "hp_api_http_" + secrets.token_hex(8)
    connection.execute(f"SAVEPOINT {savepoint}")
    try:
        yield
    except BaseException:
        connection.execute(f"ROLLBACK TO {savepoint}")
        connection.execute(f"RELEASE {savepoint}")
        raise
    else:
        connection.execute(f"RELEASE {savepoint}")


class SqliteRateLimiter:
    """Multi-process fixed-window limiter using a dedicated SQLite connection."""

    def __init__(self, connection: sqlite3.Connection, *, key_secret: bytes) -> None:
        if not isinstance(connection, sqlite3.Connection):
            raise TypeError("connection must be sqlite3.Connection")
        if not isinstance(key_secret, bytes) or len(key_secret) < 32:
            raise ConfigurationError("rate-limit key secret must contain at least 32 bytes")
        self.connection = connection
        self._key_secret = key_secret
        self.connection.execute("PRAGMA foreign_keys = ON")
        if self.connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            raise ConfigurationError("SQLite foreign-key enforcement must be enabled")
        self.connection.execute("PRAGMA trusted_schema = OFF")
        trusted = self.connection.execute("PRAGMA trusted_schema").fetchone()
        if trusted is None or trusted[0] != 0:
            raise ConfigurationError("SQLite trusted_schema must be disabled")

    def migrate(self) -> None:
        with _atomic(self.connection):
            for statement in _SCHEMA_STATEMENTS:
                self.connection.execute(statement)
            row = self.connection.execute(
                "SELECT schema_version FROM hp_api_http_rate_limit_state WHERE singleton = 1"
            ).fetchone()
            if row is None:
                self.connection.execute(
                    "INSERT INTO hp_api_http_rate_limit_state(singleton, schema_version) VALUES(1, ?)",
                    (SCHEMA_VERSION,),
                )
            elif row[0] != SCHEMA_VERSION:
                raise ConfigurationError(
                    f"unsupported HTTP rate-limit schema version: {row[0]}"
                )
            for name, expected in _EXPECTED_SCHEMA.items():
                actual = self.connection.execute(
                    "SELECT sql FROM sqlite_master WHERE name = ? AND sql IS NOT NULL",
                    (name,),
                ).fetchone()
                if actual is None or _normalized_sql(actual[0]) != expected:
                    raise ConfigurationError(
                        f"unexpected HostPanel HTTP rate-limit schema object: {name}"
                    )

    def consume(
        self,
        *,
        identity: str,
        operation_id: str,
        policy: RateLimitPolicy,
        now: int,
    ) -> RateLimitDecision:
        timestamp = unix_time(now)
        identity_value = clean_text(identity, "rate-limit identity", maximum=512)
        operation = normalize_operation_id(operation_id)
        if not isinstance(policy, RateLimitPolicy):
            raise TypeError("policy must be RateLimitPolicy")
        bucket_hash = hmac.new(
            self._key_secret,
            b"hostpanel-http-rate-limit\x00" + identity_value.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        window_start = timestamp - (timestamp % policy.window_seconds)
        reset_at = window_start + policy.window_seconds
        with _atomic(self.connection):
            # Acquire SQLite's writer lock before reading the current bucket so
            # concurrent processes cannot both observe and spend the same slot.
            self.connection.execute(
                "UPDATE hp_api_http_rate_limit_state SET schema_version = schema_version WHERE singleton = 1"
            )
            row = self.connection.execute(
                """
                SELECT window_start, request_count
                FROM hp_api_http_rate_limits
                WHERE bucket_hash = ? AND operation_id = ?
                """,
                (bucket_hash, operation),
            ).fetchone()
            if row is None:
                count = 1
                self.connection.execute(
                    """
                    INSERT INTO hp_api_http_rate_limits(
                        bucket_hash, operation_id, window_start, request_count
                    ) VALUES(?, ?, ?, 1)
                    """,
                    (bucket_hash, operation, window_start),
                )
                allowed = True
            elif row[0] != window_start:
                count = 1
                self.connection.execute(
                    """
                    UPDATE hp_api_http_rate_limits
                    SET window_start = ?, request_count = 1
                    WHERE bucket_hash = ? AND operation_id = ?
                    """,
                    (window_start, bucket_hash, operation),
                )
                allowed = True
            elif row[1] >= policy.limit:
                count = row[1]
                allowed = False
            else:
                count = row[1] + 1
                self.connection.execute(
                    """
                    UPDATE hp_api_http_rate_limits
                    SET request_count = ?
                    WHERE bucket_hash = ? AND operation_id = ?
                    """,
                    (count, bucket_hash, operation),
                )
                allowed = True
        remaining = max(0, policy.limit - count)
        return RateLimitDecision(
            allowed=allowed,
            limit=policy.limit,
            remaining=remaining,
            reset_at=reset_at,
            retry_after=0 if allowed else max(1, reset_at - timestamp),
        )

    def prune(self, *, before: int, limit: int = 1000) -> int:
        cutoff = unix_time(before)
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 10_000:
            raise ConfigurationError("rate-limit prune size is invalid")
        with _atomic(self.connection):
            rows = self.connection.execute(
                """
                SELECT bucket_hash, operation_id
                FROM hp_api_http_rate_limits
                WHERE window_start < ?
                ORDER BY window_start, operation_id
                LIMIT ?
                """,
                (cutoff, limit),
            ).fetchall()
            self.connection.executemany(
                "DELETE FROM hp_api_http_rate_limits WHERE bucket_hash = ? AND operation_id = ?",
                rows,
            )
        return len(rows)
