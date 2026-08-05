from __future__ import annotations

import ipaddress
import re
import time

from .model import MAX_RETENTION_SECONDS, RetentionPolicy, ValidationError

_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@/+~-]{0,127}\Z")
_EVENT_ID_RE = re.compile(r"aud_[A-Za-z0-9_-]{22}\Z")
_KIND_RE = re.compile(r"[a-z][a-z0-9_.:-]{0,63}\Z")
_ACTION_RE = re.compile(r"[a-z][a-z0-9_.:-]{2,127}\Z")
_REQUEST_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}\Z")

ACTOR_KINDS = frozenset({"user", "service_account", "system", "support"})
OUTCOMES = frozenset({"succeeded", "denied", "failed"})
RETENTION_CLASSES = ("standard", "security", "compliance", "permanent")


def timestamp(value: int | None) -> int:
    result = int(time.time()) if value is None else value
    if not isinstance(result, int) or isinstance(result, bool) or result < 0:
        raise ValidationError("timestamp must be a non-negative integer")
    return result


def integer(value: int, label: str, *, minimum: int, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise ValidationError(f"{label} is invalid")
    return value


def text(value: str, label: str, *, minimum: int = 1, maximum: int = 128) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{label} must be text")
    cleaned = value.strip()
    if not minimum <= len(cleaned) <= maximum or any(ord(ch) < 32 or ord(ch) == 127 for ch in cleaned):
        raise ValidationError(f"{label} is invalid")
    return cleaned


def identifier(value: str, label: str) -> str:
    cleaned = text(value, label)
    if _ID_RE.fullmatch(cleaned) is None:
        raise ValidationError(f"{label} is invalid")
    return cleaned


def optional_identifier(value: str | None, label: str) -> str | None:
    return None if value is None else identifier(value, label)


def event_id(value: str) -> str:
    if not isinstance(value, str) or _EVENT_ID_RE.fullmatch(value) is None:
        raise ValidationError("audit event ID is invalid")
    return value


def kind(value: str, label: str) -> str:
    if not isinstance(value, str) or _KIND_RE.fullmatch(value) is None:
        raise ValidationError(f"{label} is invalid")
    return value


def action(value: str) -> str:
    if not isinstance(value, str) or _ACTION_RE.fullmatch(value) is None:
        raise ValidationError("audit action is invalid")
    return value


def actor_kind(value: str) -> str:
    if value not in ACTOR_KINDS:
        raise ValidationError("audit actor kind is invalid")
    return value


def outcome(value: str) -> str:
    if value not in OUTCOMES:
        raise ValidationError("audit outcome is invalid")
    return value


def request_id(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or _REQUEST_ID_RE.fullmatch(value) is None:
        raise ValidationError("request ID is invalid")
    return value


def source_ip(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return ipaddress.ip_address(value).compressed
    except ValueError as exc:
        raise ValidationError("source IP is invalid") from exc


def target_pair(target_kind: str | None, target_id: str | None) -> tuple[str | None, str | None]:
    if (target_kind is None) != (target_id is None):
        raise ValidationError("audit target kind and ID must be provided together")
    if target_kind is None:
        return None, None
    return kind(target_kind, "audit target kind"), identifier(target_id, "audit target ID")


def retention_policy(value: RetentionPolicy) -> RetentionPolicy:
    if not isinstance(value, RetentionPolicy):
        raise ValidationError("audit retention policy is invalid")
    for name in ("standard_seconds", "security_seconds", "compliance_seconds"):
        seconds = getattr(value, name)
        integer(seconds, f"{name} retention", minimum=24 * 60 * 60, maximum=MAX_RETENTION_SECONDS)
    if value.standard_seconds > value.security_seconds or value.security_seconds > value.compliance_seconds:
        raise ValidationError("audit retention durations must be nondecreasing")
    return value


def retention_expiry(policy: RetentionPolicy, retention_class: str, occurred_at: int) -> int | None:
    if retention_class not in RETENTION_CLASSES:
        raise ValidationError("audit retention class is invalid")
    seconds = getattr(policy, f"{retention_class}_seconds")
    if seconds is None:
        return None
    result = occurred_at + seconds
    if result > 2**63 - 1:
        raise ValidationError("audit retention expiry is out of range")
    return result
