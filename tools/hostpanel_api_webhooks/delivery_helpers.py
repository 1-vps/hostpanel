from __future__ import annotations

import json
from collections.abc import Mapping

from .delivery_contracts import OutboxEventLike
from .model import DeliveryOutcome, PermanentDeliveryError, ValidationError


def webhook_body(event: OutboxEventLike) -> bytes:
    value = {
        "created_at": event.created_at,
        "data": event.payload,
        "id": event.event_id,
        "type": event.event_type,
    }
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PermanentDeliveryError("webhook payload is invalid") from exc
    if len(encoded) > 64 << 10:
        raise PermanentDeliveryError("webhook payload is too large")
    return encoded


def retryable_status(status: int) -> bool:
    return status in {408, 425, 429} or 500 <= status <= 599


def retry_after(headers: Mapping[str, str]) -> int | None:
    if not isinstance(headers, Mapping):
        raise ValidationError("webhook response headers are invalid")
    values = [value for name, value in headers.items() if isinstance(name, str) and name.lower() == "retry-after"]
    if len(values) != 1:
        return None
    value = values[0]
    if not isinstance(value, str) or not value.isascii() or not value.isdigit() or value.startswith("0"):
        return None
    parsed = int(value)
    return parsed if 1 <= parsed <= 3600 else None


def public_destination_error(error: BaseException) -> str:
    message = str(error)
    return message if message in {
        "webhook destination is disabled",
        "webhook destination is not subscribed",
        "webhook destination does not exist",
    } else "webhook destination is unavailable"


def delivery_outcome(
    event: OutboxEventLike,
    status: str,
    *,
    http_status: int | None = None,
    retry_at: int | None = None,
) -> DeliveryOutcome:
    return DeliveryOutcome(status, event.event_id, event.destination_id, http_status, retry_at)
