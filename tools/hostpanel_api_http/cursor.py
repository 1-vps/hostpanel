from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
from collections.abc import Mapping
from typing import Any

from .jsonio import canonical_json_bytes, validate_json_value
from .model import ApiProblem, ConfigurationError, identifier, operation_id, unix_time

_PREFIX = "hpc1"
_MAX_CURSOR_BYTES = 4096


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    if not isinstance(value, str) or not value or len(value) > _MAX_CURSOR_BYTES:
        raise ApiProblem(400, "invalid_cursor", "The pagination cursor is invalid.")
    if any(character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for character in value):
        raise ApiProblem(400, "invalid_cursor", "The pagination cursor is invalid.")
    padding = "=" * ((4 - len(value) % 4) % 4)
    try:
        return base64.b64decode(value + padding, altchars=b"-_", validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ApiProblem(400, "invalid_cursor", "The pagination cursor is invalid.") from exc


class CursorCodec:
    def __init__(self, secret: bytes, *, ttl_seconds: int = 900) -> None:
        if not isinstance(secret, bytes) or len(secret) < 32:
            raise ConfigurationError("cursor secret must contain at least 32 bytes")
        if (
            not isinstance(ttl_seconds, int)
            or isinstance(ttl_seconds, bool)
            or not 1 <= ttl_seconds <= 86_400
        ):
            raise ConfigurationError("cursor TTL is invalid")
        self._secret = secret
        self._ttl_seconds = ttl_seconds

    def encode(
        self,
        position: Mapping[str, Any],
        *,
        operation: str,
        principal_id: str,
        tenant_id: str,
        now: int | None = None,
    ) -> str:
        timestamp = unix_time(now)
        if not isinstance(position, Mapping) or not position:
            raise ConfigurationError("cursor position must be a non-empty object")
        normalized_position = dict(position)
        validate_json_value(normalized_position)
        payload = {
            "exp": timestamp + self._ttl_seconds,
            "op": operation_id(operation),
            "pos": normalized_position,
            "sub": identifier(principal_id, "principal ID"),
            "tenant": identifier(tenant_id, "tenant ID"),
            "v": 1,
        }
        encoded_payload = canonical_json_bytes(payload)
        if len(encoded_payload) > 2048:
            raise ConfigurationError("cursor payload is too large")
        signature = hmac.new(
            self._secret,
            _PREFIX.encode("ascii") + b"." + encoded_payload,
            hashlib.sha256,
        ).digest()
        return f"{_PREFIX}.{_b64encode(encoded_payload)}.{_b64encode(signature)}"

    def decode(
        self,
        cursor: str,
        *,
        operation: str,
        principal_id: str,
        tenant_id: str,
        now: int | None = None,
    ) -> Mapping[str, Any]:
        timestamp = unix_time(now)
        if not isinstance(cursor, str) or len(cursor.encode("ascii", errors="ignore")) > _MAX_CURSOR_BYTES:
            raise ApiProblem(400, "invalid_cursor", "The pagination cursor is invalid.")
        parts = cursor.split(".")
        if len(parts) != 3 or parts[0] != _PREFIX:
            raise ApiProblem(400, "invalid_cursor", "The pagination cursor is invalid.")
        encoded_payload = _b64decode(parts[1])
        supplied_signature = _b64decode(parts[2])
        expected_signature = hmac.new(
            self._secret,
            _PREFIX.encode("ascii") + b"." + encoded_payload,
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(supplied_signature, expected_signature):
            raise ApiProblem(400, "invalid_cursor", "The pagination cursor is invalid.")
        try:
            decoded = json.loads(encoded_payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ApiProblem(400, "invalid_cursor", "The pagination cursor is invalid.") from exc
        validate_json_value(decoded)
        if not isinstance(decoded, dict) or set(decoded) != {"exp", "op", "pos", "sub", "tenant", "v"}:
            raise ApiProblem(400, "invalid_cursor", "The pagination cursor is invalid.")
        if canonical_json_bytes(decoded) != encoded_payload:
            raise ApiProblem(400, "invalid_cursor", "The pagination cursor is invalid.")
        if decoded["v"] != 1 or not isinstance(decoded["exp"], int) or isinstance(decoded["exp"], bool):
            raise ApiProblem(400, "invalid_cursor", "The pagination cursor is invalid.")
        if decoded["exp"] <= timestamp:
            raise ApiProblem(400, "cursor_expired", "The pagination cursor has expired.")
        if decoded["exp"] > timestamp + self._ttl_seconds:
            raise ApiProblem(400, "invalid_cursor", "The pagination cursor is invalid.")
        expected = (
            operation_id(operation),
            identifier(principal_id, "principal ID"),
            identifier(tenant_id, "tenant ID"),
        )
        actual = (decoded["op"], decoded["sub"], decoded["tenant"])
        if actual != expected or not isinstance(decoded["pos"], dict) or not decoded["pos"]:
            raise ApiProblem(400, "invalid_cursor", "The pagination cursor is invalid.")
        return decoded["pos"]
