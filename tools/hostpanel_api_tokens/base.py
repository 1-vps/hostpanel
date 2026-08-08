from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from typing import Any

from .model import (
    ACCOUNT_ID_RE,
    MAX_AUDIT_PAGE,
    Actor,
    AuditEvent,
    ServiceAccount,
    ValidationError,
    encode_random,
    identifier,
)
from .schema import initialize_connection, migrate as migrate_schema


class StoreBase:
    """Shared database, audit, and query helpers for the token store."""

    connection: sqlite3.Connection

    def __init__(self, connection: sqlite3.Connection):
        if not isinstance(connection, sqlite3.Connection):
            raise TypeError("connection must be sqlite3.Connection")
        self.connection = connection
        initialize_connection(connection)

    def migrate(self) -> None:
        migrate_schema(self.connection)

    def list_audit_events(
        self,
        *,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> tuple[AuditEvent, ...]:
        if (
            not isinstance(after_sequence, int)
            or isinstance(after_sequence, bool)
            or after_sequence < 0
        ):
            raise ValidationError("audit cursor is invalid")
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or not 1 <= limit <= MAX_AUDIT_PAGE
        ):
            raise ValidationError("audit page size is invalid")
        rows = self.connection.execute(
            """
            SELECT sequence, event_id, occurred_at, actor_kind, actor_id,
                   action, target_kind, target_id, metadata_json
            FROM hp_api_token_audit_events
            WHERE sequence > ?
            ORDER BY sequence
            LIMIT ?
            """,
            (after_sequence, limit),
        ).fetchall()
        return tuple(
            AuditEvent(
                sequence,
                event_id,
                occurred_at,
                actor_kind,
                actor_id,
                action,
                target_kind,
                target_id,
                json.loads(metadata_json),
            )
            for (
                sequence,
                event_id,
                occurred_at,
                actor_kind,
                actor_id,
                action,
                target_kind,
                target_id,
                metadata_json,
            ) in rows
        )

    def _load_account(self, account_id: str) -> ServiceAccount:
        if ACCOUNT_ID_RE.fullmatch(account_id) is None:
            raise ValidationError("service-account ID is invalid")
        row = self.connection.execute(
            """
            SELECT name, enabled, allow_all_tenants, created_at, expires_at
            FROM hp_api_service_accounts
            WHERE account_id = ?
            """,
            (account_id,),
        ).fetchone()
        if row is None:
            raise ValidationError("service account does not exist")
        name, enabled, allow_all, created_at, expires_at = row
        return ServiceAccount(
            account_id,
            name,
            bool(enabled),
            self._values("hp_api_account_scopes", "scope", "account_id", account_id),
            self._values("hp_api_account_tenants", "tenant_id", "account_id", account_id),
            bool(allow_all),
            self._values("hp_api_account_cidrs", "cidr", "account_id", account_id),
            created_at,
            expires_at,
        )

    def _values(
        self,
        table: str,
        column: str,
        key: str,
        value: str,
    ) -> tuple[str, ...]:
        allowed = {
            ("hp_api_account_scopes", "scope", "account_id"),
            ("hp_api_account_tenants", "tenant_id", "account_id"),
            ("hp_api_account_cidrs", "cidr", "account_id"),
            ("hp_api_token_scopes", "scope", "token_id"),
            ("hp_api_token_tenants", "tenant_id", "token_id"),
            ("hp_api_token_cidrs", "cidr", "token_id"),
        }
        if (table, column, key) not in allowed:
            raise AssertionError("unreviewed API-token query shape")
        return tuple(
            row[0]
            for row in self.connection.execute(
                f"SELECT {column} FROM {table} WHERE {key} = ? ORDER BY {column}",
                (value,),
            )
        )

    def _audit(
        self,
        timestamp: int,
        actor: Actor,
        action: str,
        target_kind: str,
        target_id: str,
        metadata: Mapping[str, Any],
    ) -> None:
        event_id = "evt_" + encode_random(16)
        payload = json.dumps(
            metadata,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        if len(payload.encode("utf-8")) > 4096:
            raise ValidationError("audit metadata is too large")
        self.connection.execute(
            """
            INSERT INTO hp_api_token_audit_events(
                event_id, occurred_at, actor_kind, actor_id, action,
                target_kind, target_id, metadata_json
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                timestamp,
                actor.kind,
                actor.actor_id,
                identifier(action, "audit action"),
                identifier(target_kind, "audit target kind"),
                identifier(target_id, "audit target ID"),
                payload,
            ),
        )
