from __future__ import annotations

from .hashing import event_hash, genesis_hash
from .model import ChainVerification, StateError
from .redaction import decode_metadata
from .schema import validate_schema_shape


class VerifyMixin:
    def verify_chain(self) -> ChainVerification:
        validate_schema_shape(self.connection)
        previous = genesis_hash()
        count = 0
        last_sequence = 0
        previous_time = 0
        rows = self.connection.execute(
            """
            SELECT sequence, event_id, tenant_id, occurred_at, actor_kind, actor_id,
                   effective_principal_id, action, outcome, target_kind, target_id,
                   request_id, source_ip, reason_code, metadata_json, retention_class,
                   expires_at, previous_hash, event_hash
            FROM hp_api_audit_events ORDER BY sequence
            """
        ).fetchall()
        for row in rows:
            sequence = row[0]
            if sequence != last_sequence + 1 or row[3] < previous_time or bytes(row[17]) != previous:
                raise StateError("audit chain ordering or previous hash is invalid")
            metadata = decode_metadata(row[14])
            fields = {
                "event_id": row[1], "tenant_id": row[2], "occurred_at": row[3],
                "actor_kind": row[4], "actor_id": row[5], "effective_principal_id": row[6],
                "action": row[7], "outcome": row[8], "target_kind": row[9], "target_id": row[10],
                "request_id": row[11], "source_ip": row[12], "reason_code": row[13],
                "metadata": metadata, "retention_class": row[15], "expires_at": row[16],
                "previous_hash": previous.hex(),
            }
            calculated = event_hash(fields)
            if calculated != bytes(row[18]):
                raise StateError("audit event hash is invalid")
            previous = calculated
            count += 1
            last_sequence = sequence
            previous_time = row[3]
        state = self.connection.execute(
            "SELECT event_count, last_sequence, head_hash FROM hp_api_audit_state WHERE singleton = 1"
        ).fetchone()
        if state is None or state[0] != count or state[1] != last_sequence or bytes(state[2]) != previous:
            raise StateError("audit chain head state is invalid")
        return ChainVerification(count, previous, last_sequence)
