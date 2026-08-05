from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from hostpanel_api_http.model import ApiProblem, RequestContext


def body_object(context: RequestContext, *, allowed: set[str], required: set[str] = frozenset()) -> Mapping[str, Any]:
    value = context.json_body
    if not isinstance(value, Mapping):
        raise ApiProblem(400, "invalid_body", "The request body must be a JSON object.")
    keys = set(value)
    unknown = keys - allowed
    missing = required - keys
    if unknown:
        raise ApiProblem(400, "unknown_field", "The request body contains an unknown field.", details={"fields": sorted(unknown)})
    if missing:
        raise ApiProblem(400, "missing_field", "The request body is missing a required field.", details={"fields": sorted(missing)})
    return value


def text_field(value: Mapping[str, Any], name: str, *, required: bool = True, maximum: int = 128) -> str | None:
    item = value.get(name)
    if item is None and not required:
        return None
    if not isinstance(item, str):
        raise ApiProblem(400, "invalid_field", f"Field '{name}' must be text.")
    cleaned = item.strip()
    if not cleaned or len(cleaned) > maximum or any(ord(ch) < 32 or ord(ch) == 127 for ch in cleaned):
        raise ApiProblem(400, "invalid_field", f"Field '{name}' is invalid.")
    return cleaned


def integer_field(
    value: Mapping[str, Any],
    name: str,
    *,
    required: bool = True,
    minimum: int = 0,
    maximum: int = 2**63 - 1,
) -> int | None:
    item = value.get(name)
    if item is None and not required:
        return None
    if not isinstance(item, int) or isinstance(item, bool) or not minimum <= item <= maximum:
        raise ApiProblem(400, "invalid_field", f"Field '{name}' is invalid.")
    return item


def boolean_field(value: Mapping[str, Any], name: str, *, default: bool | None = None) -> bool:
    item = value.get(name, default)
    if not isinstance(item, bool):
        raise ApiProblem(400, "invalid_field", f"Field '{name}' must be boolean.")
    return item


def string_list_field(
    value: Mapping[str, Any],
    name: str,
    *,
    required: bool = True,
    maximum: int = 512,
) -> tuple[str, ...] | None:
    item = value.get(name)
    if item is None and not required:
        return None
    if not isinstance(item, list) or len(item) > maximum or any(not isinstance(entry, str) for entry in item):
        raise ApiProblem(400, "invalid_field", f"Field '{name}' must be a bounded string array.")
    return tuple(item)


def object_field(value: Mapping[str, Any], name: str, *, required: bool = True) -> Mapping[str, Any] | None:
    item = value.get(name)
    if item is None and not required:
        return None
    if not isinstance(item, Mapping):
        raise ApiProblem(400, "invalid_field", f"Field '{name}' must be an object.")
    return item


def single_query(context: RequestContext, name: str) -> str | None:
    values = context.query_parameters.get(name, ())
    if len(values) > 1:
        raise ApiProblem(400, "invalid_query", f"Query parameter '{name}' must not be repeated.")
    return values[0] if values else None


def decimal_query(context: RequestContext, name: str, *, minimum: int, maximum: int) -> int | None:
    raw = single_query(context, name)
    if raw is None:
        return None
    if not raw.isascii() or not raw.isdecimal() or (len(raw) > 1 and raw.startswith("0")):
        raise ApiProblem(400, "invalid_query", f"Query parameter '{name}' is invalid.")
    value = int(raw)
    if not minimum <= value <= maximum:
        raise ApiProblem(400, "invalid_query", f"Query parameter '{name}' is invalid.")
    return value
