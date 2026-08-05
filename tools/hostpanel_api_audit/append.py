from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from typing import Any

from .hashing import event_hash
from .model import AuditEvent, ConflictError, StateError
from .redaction import redact_metadata
from .schema import atomic
from .validation import (
    action as normalize_action,
    actor_kind as normalize_actor_kind,
    identifier,
    kind,
    optional_identifier,
    outcome as normalize_outcome,
    request_id as normalize_request_id,
    retention_expiry,
    source_ip as normalize_source_ip,
    target_pair,
    timestamp,
)


class AppendMixin:
    def append_event(
        self,
        *,
        tenant_id: str,
        actor_kind: str,
        actor_id: str,
        action: str,
        outcome: str,
        metadata: Mapping[str, Any] | None = None,
        effective_principal_id: str | None = None,
        target_kind: str | None = None,
        target_id: str | None = None,
        request_id: str | None = None,
        source_ip: str | None = None,
        reason_code: str | None = None,
        retention_class: str = "standard",
        occurred_at: int | None = None,
    ) -> AuditEvent:
        at = timestamp(occurred_at)
        tenant = identifier(tenant_id, "tenant ID")
        actor_type = normalize_actor_kind(actor_kind)
        actor = identifier(actor_id, "actor ID")
        effective = optional_identifier(effective_principal_id, "effective principal ID")
        operation = normalize_action(action)
        result = normalize_outcome(outcome)
        target_type, target = target_pair(target_kind, target_id)
        request = normalize_request_id(request_id)
        address = normalize_source_ip(source_ip)
        reason = None if reason_code is None else kind(reason_code, "audit reason code")
        redacted, metadata_json = redact_metadata(metadata)
        expiry = retention_expiry(self.policy, retention_class, at)
        event_id = self.new_event_id()

        with atomic(self.connection):
            # Acquire SQLite's writer lock before reading the chain head.
            self.connection.execute("UPDATE hp_api_audit_state SET event_count = event_count WHERE singleton = 1")
            state = self.connection.execute(
                "SELECT event_count, last_sequence, head_hash FROM hp_api_audit_state WHERE singleton = 1"
            ).fetchone()
            if state is None:
                raise StateError("audit schema is not migrated")
            event_count, last_sequence, previous_hash = state
            last = self.connection.execute(
                "SELECT sequence, occurred_at, event_hash FROM hp_api_audit_events ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            if last is None:
                if event_count != 0 or last_sequence != 0 or bytes(previous_hash) != self.genesis_hash():
                    raise StateError("audit chain head state is inconsistent")
            else:
                if (
                    event_count != last_sequence
                    or last[0] != last_sequence
                    or bytes(last[2]) != bytes(previous_hash)
                ):
                    raise StateError("audit chain head state is inconsistent")
                if at < last[1]:
                    raise StateError("audit timestamps cannot move backwards")
            fields = {
                "event_id": event_id,
                "tenant_id": tenant,
                "occurred_at": at,
                "actor_kind": actor_type,
                "actor_id": actor,
                "effective_principal_id": effective,
                "action": operation,
                "outcome": result,
                "target_kind": target_type,
                "target_id": target,
                "request_id": request,
                "source_ip": address,
                "reason_code": reason,
                "metadata": redacted,
                "retention_class": retention_class,
                "expires_at": expiry,
                "previous_hash": bytes(previous_hash).hex(),
            }
            digest = event_hash(fields)
            try:
                cursor = self.connection.execute(
                    """
                    INSERT INTO hp_api_audit_events(
                        event_id, tenant_id, occurred_at, actor_kind, actor_id,
                        effective_principal_id, action, outcome, target_kind, target_id,
                        request_id, source_ip, reason_code, metadata_json,
                        retention_class, expires_at, previous_hash, event_hash
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id, tenant, at, actor_type, actor, effective, operation,
                        result, target_type, target, request, address, reason, metadata_json,
                        retention_class, expiry, previous_hash, digest,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ConflictError("audit event could not be appended") from exc
            sequence = cursor.lastrowid
            if sequence != last_sequence + 1 or event_count != last_sequence:
                raise StateError("audit sequence state is inconsistent")
            updated = self.connection.execute(
                """
                UPDATE hp_api_audit_state
                SET event_count = ?, last_sequence = ?, head_hash = ?
                WHERE singleton = 1 AND event_count = ? AND last_sequence = ? AND head_hash = ?
                """,
                (event_count + 1, sequence, digest, event_count, last_sequence, previous_hash),
            )
            if updated.rowcount != 1:
                raise StateError("audit chain head changed concurrently")
        return self.get_event(event_id, tenant_id=tenant)
