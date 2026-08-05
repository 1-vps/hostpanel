from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@/+~-]{0,127}\Z")
_TYPE_RE = re.compile(r"[a-z][a-z0-9_.-]{0,127}\Z")
_SECRET_REF_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/@+~-]{7,255}\Z")


class WebhookError(RuntimeError):
    pass


class ValidationError(WebhookError):
    pass


class ConflictError(WebhookError):
    pass


class StateError(WebhookError):
    pass


class TransientDeliveryError(WebhookError):
    pass


class PermanentDeliveryError(WebhookError):
    pass


def identifier(value: str, label: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{label} must be text")
    cleaned = value.strip()
    if _ID_RE.fullmatch(cleaned) is None:
        raise ValidationError(f"{label} is invalid")
    return cleaned


def event_type(value: str) -> str:
    if not isinstance(value, str) or _TYPE_RE.fullmatch(value) is None:
        raise ValidationError("event type is invalid")
    return value


def secret_ref(value: str) -> str:
    if not isinstance(value, str) or _SECRET_REF_RE.fullmatch(value) is None:
        raise ValidationError("secret reference is invalid")
    return value


def timestamp(value: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValidationError("timestamp is invalid")
    return value


def positive_int(value: int, label: str, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= maximum:
        raise ValidationError(f"{label} is invalid")
    return value


def json_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValidationError("webhook payload must be an object")
    return value
