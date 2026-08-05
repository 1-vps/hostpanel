from __future__ import annotations

import json

from .model import (
    MAX_IDEMPOTENCY_TTL,
    MAX_RESPONSE_BODY,
    ConflictError,
    IdempotencyDecision,
    StateError,
    ValidationError,
    decode_response_headers,
    encode_random,
    identifier,
    idempotency_key as normalize_key,
    integer,
    method,
    now as normalize_now,
    request_fingerprint,
    response_headers,
    route,
)
from .schema import atomic


class IdempotencyMixin:
    def begin_idempotent_request(
        self,
        *,
        principal_id: str,
        key: str,
        http_method: str,
        request_route: str,
        request_body: bytes,
        expires_at: int,
        now: int | None = None,
    ) -> IdempotencyDecision:
        timestamp = normalize_now(now)
        expiry = normalize_now(expires_at)
        if expiry <= timestamp or expiry - timestamp > MAX_IDEMPOTENCY_TTL:
            raise ValidationError("idempotency expiry is invalid")
        principal = identifier(principal_id, "principal ID")
        normalized_key = normalize_key(key)
        verb = method(http_method)
        path = route(request_route)
        fingerprint = request_fingerprint(verb, path, request_body)

        with atomic(self.connection):
            self.connection.execute(
                """
                DELETE FROM hp_api_idempotency
                WHERE principal_id = ? AND idempotency_key = ?
                  AND expires_at <= ?
                """,
                (principal, normalized_key, timestamp),
            )
            record_id = encode_random("idem_")
            inserted = self.connection.execute(
                """
                INSERT INTO hp_api_idempotency(
                    record_id, principal_id, idempotency_key, request_hash,
                    http_method, route, state, created_at, expires_at
                ) VALUES(?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                ON CONFLICT(principal_id, idempotency_key) DO NOTHING
                """,
                (
                    record_id,
                    principal,
                    normalized_key,
                    fingerprint,
                    verb,
                    path,
                    timestamp,
                    expiry,
                ),
            )
            if inserted.rowcount == 1:
                return IdempotencyDecision(record_id, True, "pending")

            row = self.connection.execute(
                """
                SELECT record_id, request_hash, http_method, route, state,
                       response_status, response_headers_json, response_body
                FROM hp_api_idempotency
                WHERE principal_id = ? AND idempotency_key = ?
                """,
                (principal, normalized_key),
            ).fetchone()
            if row is None:
                raise StateError("idempotency conflict could not be resolved")
            (
                existing_id,
                stored_hash,
                stored_method,
                stored_route,
                state,
                response_status,
                headers_json,
                response_body,
            ) = row
            if (
                bytes(stored_hash) != fingerprint
                or stored_method != verb
                or stored_route != path
            ):
                raise ConflictError(
                    "idempotency key was already used for a different request"
                )
            headers = decode_response_headers(headers_json)
            return IdempotencyDecision(
                existing_id,
                False,
                state,
                response_status,
                headers,
                None if response_body is None else bytes(response_body),
            )

    def complete_idempotent_request(
        self,
        record_id: str,
        *,
        response_status: int,
        response_body: bytes,
        response_headers_map: dict[str, str] | None = None,
        now: int | None = None,
    ) -> IdempotencyDecision:
        timestamp = normalize_now(now)
        record = identifier(record_id, "idempotency record ID")
        status = integer(response_status, "response status", minimum=100, maximum=599)
        if not isinstance(response_body, bytes) or len(response_body) > MAX_RESPONSE_BODY:
            raise ValidationError("response body is invalid")
        headers_json = response_headers(response_headers_map)
        with atomic(self.connection):
            result = self.connection.execute(
                """
                UPDATE hp_api_idempotency
                SET state = 'completed', completed_at = ?, response_status = ?,
                    response_headers_json = ?, response_body = ?
                WHERE record_id = ? AND state = 'pending' AND created_at <= ? AND expires_at > ?
                """,
                (
                    timestamp,
                    status,
                    headers_json,
                    response_body,
                    record,
                    timestamp,
                    timestamp,
                ),
            )
            if result.rowcount != 1:
                raise StateError(
                    "idempotency record is missing, expired, or already completed"
                )
        return IdempotencyDecision(
            record,
            False,
            "completed",
            status,
            json.loads(headers_json),
            response_body,
        )

    def prune_idempotency(self, *, before: int, limit: int = 1000) -> int:
        cutoff = normalize_now(before)
        page = integer(limit, "prune limit", minimum=1, maximum=1000)
        with atomic(self.connection):
            rows = self.connection.execute(
                """
                SELECT record_id FROM hp_api_idempotency
                WHERE expires_at <= ?
                ORDER BY expires_at, record_id
                LIMIT ?
                """,
                (cutoff, page),
            ).fetchall()
            if rows:
                self.connection.executemany(
                    "DELETE FROM hp_api_idempotency WHERE record_id = ?",
                    rows,
                )
            return len(rows)
