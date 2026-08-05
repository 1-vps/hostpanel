from __future__ import annotations

import hashlib
import hmac
import re

from .model import (
    EVENT_ID_RE,
    MAX_WEBHOOK_TOLERANCE,
    AuthenticationError,
    ValidationError,
    identifier,
    integer,
    now as normalize_now,
)

_SIGNATURE_RE = re.compile(r"t=([0-9]{1,20}),v1=([0-9a-f]{64})\Z")


def _secret(value: bytes) -> bytes:
    if not isinstance(value, bytes) or not 32 <= len(value) <= 128:
        raise ValidationError("webhook secret is invalid")
    return value


def _message(event_id: str, timestamp: int, payload: bytes) -> bytes:
    event = identifier(event_id, "event ID")
    if EVENT_ID_RE.fullmatch(event) is None:
        raise ValidationError("event ID is invalid")
    if not isinstance(payload, bytes) or len(payload) > 64 << 10:
        raise ValidationError("webhook payload is invalid")
    return (
        b"hostpanel-webhook-v1\n"
        + str(timestamp).encode("ascii")
        + b"\n"
        + event.encode("ascii")
        + b"\n"
        + hashlib.sha256(payload).hexdigest().encode("ascii")
    )


def sign_webhook(
    secret: bytes,
    *,
    event_id: str,
    payload: bytes,
    timestamp: int,
) -> str:
    key = _secret(secret)
    at = normalize_now(timestamp)
    signature = hmac.new(key, _message(event_id, at, payload), hashlib.sha256).hexdigest()
    return f"t={at},v1={signature}"


def verify_webhook_signature(
    secret: bytes,
    *,
    event_id: str,
    payload: bytes,
    signature_header: str,
    now: int | None = None,
    tolerance_seconds: int = 300,
) -> int:
    key = _secret(secret)
    timestamp_now = normalize_now(now)
    tolerance = integer(
        tolerance_seconds,
        "webhook tolerance",
        minimum=0,
        maximum=MAX_WEBHOOK_TOLERANCE,
    )
    if not isinstance(signature_header, str):
        raise AuthenticationError("invalid webhook signature")
    match = _SIGNATURE_RE.fullmatch(signature_header)
    if match is None:
        raise AuthenticationError("invalid webhook signature")
    timestamp = int(match.group(1))
    if abs(timestamp_now - timestamp) > tolerance:
        raise AuthenticationError("invalid webhook signature")
    try:
        message = _message(event_id, timestamp, payload)
    except ValidationError as exc:
        raise AuthenticationError("invalid webhook signature") from exc
    expected = hmac.new(key, message, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, match.group(2)):
        raise AuthenticationError("invalid webhook signature")
    return timestamp
