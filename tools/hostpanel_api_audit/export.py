from __future__ import annotations

import csv
import hashlib
import io
import json

from .model import AuditEvent, MAX_EXPORT_EVENTS, ValidationError

_FIELDS = (
    "sequence", "event_id", "tenant_id", "occurred_at", "actor_kind", "actor_id",
    "effective_principal_id", "action", "outcome", "target_kind", "target_id",
    "request_id", "source_ip", "reason_code", "metadata", "retention_class",
    "expires_at", "previous_hash", "event_hash",
)


def _record(event: AuditEvent) -> dict:
    return {
        "sequence": event.sequence,
        "event_id": event.event_id,
        "tenant_id": event.tenant_id,
        "occurred_at": event.occurred_at,
        "actor_kind": event.actor_kind,
        "actor_id": event.actor_id,
        "effective_principal_id": event.effective_principal_id,
        "action": event.action,
        "outcome": event.outcome,
        "target_kind": event.target_kind,
        "target_id": event.target_id,
        "request_id": event.request_id,
        "source_ip": event.source_ip,
        "reason_code": event.reason_code,
        "metadata": event.metadata,
        "retention_class": event.retention_class,
        "expires_at": event.expires_at,
        "previous_hash": event.previous_hash.hex(),
        "event_hash": event.event_hash.hex(),
    }


def _events(value) -> tuple[AuditEvent, ...]:
    events = tuple(value)
    if len(events) > MAX_EXPORT_EVENTS or any(not isinstance(event, AuditEvent) for event in events):
        raise ValidationError("audit export event set is invalid")
    return events


def _csv_safe(value) -> str:
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(("=", "+", "-", "@")) else text


class ExportMixin:
    def export_jsonl(self, events) -> bytes:
        output = b"".join(
            json.dumps(_record(event), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8") + b"\n"
            for event in _events(events)
        )
        return output

    def export_csv(self, events) -> bytes:
        rows = _events(events)
        fields = _FIELDS
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for event in rows:
            record = _record(event)
            record["metadata"] = json.dumps(record["metadata"], sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            writer.writerow({key: _csv_safe(record[key]) for key in fields})
        return stream.getvalue().encode("utf-8")

    @staticmethod
    def export_digest(payload: bytes) -> str:
        if not isinstance(payload, bytes):
            raise ValidationError("audit export payload must be bytes")
        return hashlib.sha256(payload).hexdigest()
