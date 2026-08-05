from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import Any


def json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool, float)):
        return value
    if isinstance(value, bytes):
        return value.hex()
    if dataclasses.is_dataclass(value):
        return {field.name: json_value(getattr(value, field.name)) for field in dataclasses.fields(value)}
    if isinstance(value, Mapping):
        return {str(key): json_value(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (tuple, list)):
        return [json_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [json_value(item) for item in sorted(value)]
    raise TypeError(f"unsupported response value: {type(value).__name__}")


def envelope(name: str, value: Any, **extra: Any) -> dict[str, Any]:
    payload = {name: json_value(value)}
    payload.update({key: json_value(item) for key, item in extra.items()})
    return payload
