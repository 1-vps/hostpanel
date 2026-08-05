from __future__ import annotations

from .model import AuditEvent, MAX_PAGE_SIZE, StateError, ValidationError
from .redaction import decode_metadata
from .validation import (
    action as normalize_action,
    actor_kind as normalize_actor_kind,
    event_id as normalize_event_id,
    identifier,
    integer,
    kind,
    outcome as normalize_outcome,
    request_id as normalize_request_id,
    RETENTION_CLASSES,
    target_pair,
    timestamp,
)

_EVENT_COLUMNS = """
sequence, event_id, tenant_id, occurred_at, actor_kind, actor_id,
effective_principal_id, action, outcome, target_kind, target_id, request_id,
source_ip, reason_code, metadata_json, retention_class, expires_at,
previous_hash, event_hash
"""


class QueryMixin:
    def get_event(self, event_id: str, *, tenant_id: str) -> AuditEvent:
        tenant = identifier(tenant_id, "tenant ID")
        row = self.connection.execute(
            f"SELECT {_EVENT_COLUMNS} FROM hp_api_audit_events WHERE event_id = ? AND tenant_id = ?",
            (normalize_event_id(event_id), tenant),
        ).fetchone()
        if row is None:
            raise StateError("audit event does not exist")
        return self._event(row)

    def search_events(
        self,
        *,
        tenant_id: str,
        actor_kind: str | None = None,
        actor_id: str | None = None,
        effective_principal_id: str | None = None,
        action: str | None = None,
        outcome: str | None = None,
        target_kind: str | None = None,
        target_id: str | None = None,
        request_id: str | None = None,
        reason_code: str | None = None,
        retention_class: str | None = None,
        from_at: int | None = None,
        to_at: int | None = None,
        after: tuple[int, int] | None = None,
        limit: int = 100,
    ) -> tuple[AuditEvent, ...]:
        tenant = identifier(tenant_id, "tenant ID")
        page = integer(limit, "audit page size", minimum=1, maximum=MAX_PAGE_SIZE)
        conditions = ["tenant_id = ?"]
        arguments: list[object] = [tenant]
        if actor_kind is not None:
            conditions.append("actor_kind = ?")
            arguments.append(normalize_actor_kind(actor_kind))
        if actor_id is not None:
            conditions.append("actor_id = ?")
            arguments.append(identifier(actor_id, "actor ID"))
        if effective_principal_id is not None:
            conditions.append("effective_principal_id = ?")
            arguments.append(identifier(effective_principal_id, "effective principal ID"))
        if action is not None:
            conditions.append("action = ?")
            arguments.append(normalize_action(action))
        if outcome is not None:
            conditions.append("outcome = ?")
            arguments.append(normalize_outcome(outcome))
        if target_kind is not None or target_id is not None:
            target_type, target = target_pair(target_kind, target_id)
            conditions.extend(("target_kind = ?", "target_id = ?"))
            arguments.extend((target_type, target))
        if request_id is not None:
            conditions.append("request_id = ?")
            arguments.append(normalize_request_id(request_id))
        if reason_code is not None:
            conditions.append("reason_code = ?")
            arguments.append(kind(reason_code, "audit reason code"))
        if retention_class is not None:
            if retention_class not in RETENTION_CLASSES:
                raise ValidationError("audit retention class is invalid")
            conditions.append("retention_class = ?")
            arguments.append(retention_class)
        if from_at is not None:
            conditions.append("occurred_at >= ?")
            arguments.append(timestamp(from_at))
        if to_at is not None:
            conditions.append("occurred_at <= ?")
            arguments.append(timestamp(to_at))
        if from_at is not None and to_at is not None and from_at > to_at:
            raise ValidationError("audit time range is invalid")
        if after is not None:
            if not isinstance(after, tuple) or len(after) != 2:
                raise ValidationError("audit cursor is invalid")
            cursor_time = timestamp(after[0])
            cursor_sequence = integer(after[1], "audit cursor sequence", minimum=0, maximum=2**63 - 1)
            conditions.append("(occurred_at > ? OR (occurred_at = ? AND sequence > ?))")
            arguments.extend((cursor_time, cursor_time, cursor_sequence))
        arguments.append(page)
        rows = self.connection.execute(
            f"SELECT {_EVENT_COLUMNS} FROM hp_api_audit_events WHERE {' AND '.join(conditions)} ORDER BY occurred_at, sequence LIMIT ?",
            arguments,
        ).fetchall()
        return tuple(self._event(row) for row in rows)

    @staticmethod
    def _event(row) -> AuditEvent:
        return AuditEvent(
            sequence=row[0], event_id=row[1], tenant_id=row[2], occurred_at=row[3],
            actor_kind=row[4], actor_id=row[5], effective_principal_id=row[6],
            action=row[7], outcome=row[8], target_kind=row[9], target_id=row[10],
            request_id=row[11], source_ip=row[12], reason_code=row[13],
            metadata=decode_metadata(row[14]), retention_class=row[15],
            expires_at=row[16], previous_hash=bytes(row[17]), event_hash=bytes(row[18]),
        )
