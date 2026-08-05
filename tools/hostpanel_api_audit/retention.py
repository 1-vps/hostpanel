from __future__ import annotations

import hashlib
import json

from .model import MAX_EXPORT_EVENTS, MAX_PAGE_SIZE, RetentionCandidate, ValidationError
from .validation import identifier, integer, timestamp


class RetentionMixin:
    def retention_candidates(
        self,
        *,
        before: int,
        tenant_id: str | None = None,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> tuple[RetentionCandidate, ...]:
        cutoff = timestamp(before)
        cursor = integer(after_sequence, "retention cursor", minimum=0, maximum=2**63 - 1)
        page = integer(limit, "retention page size", minimum=1, maximum=MAX_PAGE_SIZE)
        conditions = ["expires_at IS NOT NULL", "expires_at <= ?", "sequence > ?"]
        arguments: list[object] = [cutoff, cursor]
        if tenant_id is not None:
            conditions.append("tenant_id = ?")
            arguments.append(identifier(tenant_id, "tenant ID"))
        arguments.append(page)
        rows = self.connection.execute(
            f"SELECT sequence, event_id, tenant_id, occurred_at, expires_at, event_hash FROM hp_api_audit_events WHERE {' AND '.join(conditions)} ORDER BY sequence LIMIT ?",
            arguments,
        ).fetchall()
        return tuple(RetentionCandidate(row[0], row[1], row[2], row[3], row[4], bytes(row[5])) for row in rows)

    @staticmethod
    def retention_manifest(candidates) -> bytes:
        items = tuple(candidates)
        if len(items) > MAX_EXPORT_EVENTS or any(not isinstance(item, RetentionCandidate) for item in items):
            raise ValidationError("audit retention candidate set is invalid")
        if any(items[index].sequence >= items[index + 1].sequence for index in range(len(items) - 1)):
            raise ValidationError("audit retention candidates must be strictly ordered")
        document = {
            "count": len(items),
            "first_sequence": items[0].sequence if items else None,
            "last_sequence": items[-1].sequence if items else None,
            "format": "hostpanel-audit-retention-v1",
            "events": [
                {
                    "sequence": item.sequence,
                    "event_id": item.event_id,
                    "tenant_id": item.tenant_id,
                    "occurred_at": item.occurred_at,
                    "expires_at": item.expires_at,
                    "event_hash": item.event_hash.hex(),
                }
                for item in items
            ],
        }
        return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8") + b"\n"

    @staticmethod
    def retention_manifest_digest(payload: bytes) -> str:
        if not isinstance(payload, bytes):
            raise ValidationError("audit retention manifest must be bytes")
        return hashlib.sha256(payload).hexdigest()
