from __future__ import annotations

import sqlite3

from .model import (
    EVENT_ID_RE,
    MAX_ATTEMPTS,
    MAX_LEASE_SECONDS,
    MAX_OUTBOX_PAGE,
    MAX_RECEIPT_TTL,
    ConflictError,
    OutboxCreation,
    OutboxEvent,
    ReplayError,
    StateError,
    ValidationError,
    canonical_json,
    decode_json,
    encode_random,
    identifier,
    integer,
    kind,
    now as normalize_now,
    optional_key,
    text,
)
from .schema import atomic
from .signing import verify_webhook_signature


class OutboxMixin:
    def enqueue_outbox_event(
        self,
        *,
        destination_id: str,
        event_type: str,
        payload: dict,
        dedupe_key: str | None = None,
        max_attempts: int = 10,
        available_at: int | None = None,
        now: int | None = None,
    ) -> OutboxCreation:
        timestamp = normalize_now(now)
        available = timestamp if available_at is None else normalize_now(available_at)
        if available < timestamp:
            raise ValidationError("outbox availability cannot predate creation")
        destination = identifier(destination_id, "destination ID")
        category = kind(event_type, "event type")
        dedupe = optional_key(dedupe_key)
        attempts_limit = integer(
            max_attempts,
            "maximum attempts",
            minimum=1,
            maximum=MAX_ATTEMPTS,
        )
        payload_json, payload_hash = canonical_json(payload, "outbox payload")
        event_id = encode_random("evt_")
        with atomic(self.connection):
            try:
                self.connection.execute(
                    """
                    INSERT INTO hp_api_outbox(
                        event_id, destination_id, event_type, dedupe_key,
                        payload_json, payload_hash, status, created_at,
                        available_at, attempts, max_attempts
                    ) VALUES(?, ?, ?, ?, ?, ?, 'pending', ?, ?, 0, ?)
                    """,
                    (
                        event_id,
                        destination,
                        category,
                        dedupe,
                        payload_json,
                        payload_hash,
                        timestamp,
                        available,
                        attempts_limit,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                if dedupe is None:
                    raise
                row = self.connection.execute(
                    """
                    SELECT event_id, event_type, payload_hash
                    FROM hp_api_outbox
                    WHERE destination_id = ? AND dedupe_key = ?
                    """,
                    (destination, dedupe),
                ).fetchone()
                if row is None:
                    raise
                if row[1] != category or bytes(row[2]) != payload_hash:
                    raise ConflictError(
                        "outbox dedupe key was reused for a different event"
                    ) from exc
                return OutboxCreation(self._load_outbox(row[0]), False)
        return OutboxCreation(self._load_outbox(event_id), True)

    def claim_outbox_event(
        self,
        *,
        worker_id: str,
        lease_seconds: int = 60,
        now: int | None = None,
    ) -> OutboxEvent | None:
        timestamp = normalize_now(now)
        worker = identifier(worker_id, "worker ID")
        lease = integer(
            lease_seconds,
            "lease duration",
            minimum=1,
            maximum=MAX_LEASE_SECONDS,
        )
        with atomic(self.connection):
            self.connection.execute(
                """
                UPDATE hp_api_outbox
                SET status = CASE
                      WHEN attempts < max_attempts THEN 'retry_wait'
                      ELSE 'failed'
                    END,
                    available_at = ?,
                    last_error = 'delivery lease expired',
                    lease_owner = NULL, lease_expires_at = NULL
                WHERE status = 'delivering' AND lease_expires_at < ?
                """,
                (timestamp, timestamp),
            )
            row = self.connection.execute(
                """
                SELECT event_id FROM hp_api_outbox
                WHERE status IN ('pending', 'retry_wait') AND available_at <= ?
                ORDER BY available_at, created_at, event_id LIMIT 1
                """,
                (timestamp,),
            ).fetchone()
            if row is None:
                return None
            result = self.connection.execute(
                """
                UPDATE hp_api_outbox
                SET status = 'delivering', attempts = attempts + 1,
                    lease_owner = ?, lease_expires_at = ?
                WHERE event_id = ? AND status IN ('pending', 'retry_wait')
                  AND available_at <= ?
                """,
                (worker, timestamp + lease, row[0], timestamp),
            )
            if result.rowcount != 1:
                return None
            return self._load_outbox(row[0])

    def mark_outbox_delivered(
        self,
        event_id: str,
        *,
        worker_id: str,
        now: int | None = None,
    ) -> OutboxEvent:
        timestamp = normalize_now(now)
        event = self._event_id(event_id)
        worker = identifier(worker_id, "worker ID")
        with atomic(self.connection):
            result = self.connection.execute(
                """
                UPDATE hp_api_outbox
                SET status = 'delivered', delivered_at = ?, last_error = NULL,
                    lease_owner = NULL, lease_expires_at = NULL
                WHERE event_id = ? AND status = 'delivering'
                  AND lease_owner = ? AND lease_expires_at >= ?
                  AND created_at <= ?
                """,
                (timestamp, event, worker, timestamp, timestamp),
            )
            if result.rowcount != 1:
                raise StateError("outbox lease is invalid")
            return self._load_outbox(event)

    def fail_outbox_event(
        self,
        event_id: str,
        *,
        worker_id: str,
        error: str,
        retry_at: int | None = None,
        now: int | None = None,
    ) -> OutboxEvent:
        timestamp = normalize_now(now)
        event = self._event_id(event_id)
        worker = identifier(worker_id, "worker ID")
        error_text = text(error, "outbox error", maximum=4096)
        retry = None if retry_at is None else normalize_now(retry_at)
        if retry is not None and retry <= timestamp:
            raise ValidationError("retry time must be in the future")
        with atomic(self.connection):
            row = self.connection.execute(
                """
                SELECT attempts, max_attempts FROM hp_api_outbox
                WHERE event_id = ? AND status = 'delivering'
                  AND lease_owner = ? AND lease_expires_at >= ?
                  AND created_at <= ?
                """,
                (event, worker, timestamp, timestamp),
            ).fetchone()
            if row is None:
                raise StateError("outbox lease is invalid")
            if retry is not None and row[0] < row[1]:
                status, available = "retry_wait", retry
            else:
                status, available = "failed", timestamp
            self.connection.execute(
                """
                UPDATE hp_api_outbox
                SET status = ?, available_at = ?, last_error = ?,
                    lease_owner = NULL, lease_expires_at = NULL
                WHERE event_id = ?
                """,
                (status, available, error_text, event),
            )
            return self._load_outbox(event)

    def list_outbox_events(
        self,
        *,
        destination_id: str | None = None,
        after: tuple[int, str] | None = None,
        limit: int = 100,
    ) -> tuple[OutboxEvent, ...]:
        destination = (
            None
            if destination_id is None
            else identifier(destination_id, "destination ID")
        )
        page = integer(limit, "outbox page size", minimum=1, maximum=MAX_OUTBOX_PAGE)
        if after is None:
            cursor_time, cursor_id = -1, ""
        else:
            if not isinstance(after, tuple) or len(after) != 2:
                raise ValidationError("outbox cursor is invalid")
            cursor_time = normalize_now(after[0])
            cursor_id = self._event_id(after[1])
        conditions = ["(created_at > ? OR (created_at = ? AND event_id > ?))"]
        arguments: list[object] = [cursor_time, cursor_time, cursor_id]
        if destination is not None:
            conditions.append("destination_id = ?")
            arguments.append(destination)
        arguments.append(page)
        rows = self.connection.execute(
            f"SELECT event_id FROM hp_api_outbox WHERE {' AND '.join(conditions)} ORDER BY created_at, event_id LIMIT ?",
            arguments,
        ).fetchall()
        return tuple(self._load_outbox(row[0]) for row in rows)

    def verify_and_record_webhook(
        self,
        *,
        receiver_id: str,
        event_id: str,
        payload: bytes,
        signature_header: str,
        secret: bytes,
        now: int | None = None,
        tolerance_seconds: int = 300,
        receipt_ttl: int = 24 * 60 * 60,
    ) -> int:
        timestamp_now = normalize_now(now)
        receiver = identifier(receiver_id, "receiver ID")
        event = self._event_id(event_id)
        ttl = integer(
            receipt_ttl,
            "webhook receipt TTL",
            minimum=1,
            maximum=MAX_RECEIPT_TTL,
        )
        signed_at = verify_webhook_signature(
            secret,
            event_id=event,
            payload=payload,
            signature_header=signature_header,
            now=timestamp_now,
            tolerance_seconds=tolerance_seconds,
        )
        with atomic(self.connection):
            self.connection.execute(
                "DELETE FROM hp_api_webhook_receipts WHERE expires_at <= ?",
                (timestamp_now,),
            )
            try:
                self.connection.execute(
                    """
                    INSERT INTO hp_api_webhook_receipts(
                        receiver_id, event_id, received_at, expires_at
                    ) VALUES(?, ?, ?, ?)
                    """,
                    (receiver, event, timestamp_now, timestamp_now + ttl),
                )
            except sqlite3.IntegrityError as exc:
                raise ReplayError("webhook event was already consumed") from exc
        return signed_at

    def _event_id(self, value: str) -> str:
        event = identifier(value, "event ID")
        if EVENT_ID_RE.fullmatch(event) is None:
            raise ValidationError("event ID is invalid")
        return event

    def _load_outbox(self, event_id: str) -> OutboxEvent:
        row = self.connection.execute(
            """
            SELECT destination_id, event_type, dedupe_key, payload_json, status,
                   created_at, available_at, attempts, max_attempts,
                   lease_owner, lease_expires_at, delivered_at, last_error
            FROM hp_api_outbox WHERE event_id = ?
            """,
            (event_id,),
        ).fetchone()
        if row is None:
            raise StateError("outbox event does not exist")
        return OutboxEvent(
            event_id,
            row[0],
            row[1],
            row[2],
            decode_json(row[3]) or {},
            row[4],
            row[5],
            row[6],
            row[7],
            row[8],
            row[9],
            row[10],
            row[11],
            row[12],
        )
