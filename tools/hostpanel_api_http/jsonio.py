from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import Any

from .model import ApiProblem, ConfigurationError

DEFAULT_MAX_BODY_BYTES = 1024 * 1024
MAX_JSON_DEPTH = 32
MAX_JSON_NODES = 10_000
MAX_JSON_STRING = 256 * 1024


class _DuplicateKey(ValueError):
    pass


def _object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey(key)
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite number: {value}")


def validate_json_value(value: Any) -> None:
    nodes = 0
    stack: list[tuple[Any, int]] = [(value, 1)]
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > MAX_JSON_NODES:
            raise ApiProblem(400, "invalid_json", "The JSON document is too complex.")
        if depth > MAX_JSON_DEPTH:
            raise ApiProblem(400, "invalid_json", "The JSON document is too deeply nested.")
        if current is None or isinstance(current, bool):
            continue
        if isinstance(current, int):
            if not -(2**63) <= current <= 2**63 - 1:
                raise ApiProblem(400, "invalid_json", "A JSON integer is outside the supported range.")
            continue
        if isinstance(current, float):
            if not math.isfinite(current):
                raise ApiProblem(400, "invalid_json", "A JSON number is invalid.")
            continue
        if isinstance(current, str):
            if len(current.encode("utf-8")) > MAX_JSON_STRING:
                raise ApiProblem(400, "invalid_json", "A JSON string is too large.")
            continue
        if isinstance(current, list):
            stack.extend((item, depth + 1) for item in current)
            continue
        if isinstance(current, dict):
            for key, item in current.items():
                if not isinstance(key, str):
                    raise ApiProblem(400, "invalid_json", "A JSON object key is invalid.")
                if len(key.encode("utf-8")) > 1024:
                    raise ApiProblem(400, "invalid_json", "A JSON object key is too large.")
                stack.append((item, depth + 1))
            continue
        raise ApiProblem(400, "invalid_json", "The JSON document contains an unsupported value.")


def parse_json_object(
    body: bytes,
    content_type: str | None,
    *,
    max_bytes: int = DEFAULT_MAX_BODY_BYTES,
) -> Mapping[str, Any]:
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or not 1 <= max_bytes <= 16 * 1024 * 1024:
        raise ConfigurationError("JSON body limit is invalid")
    if len(body) > max_bytes:
        raise ApiProblem(413, "payload_too_large", "The request body is too large.")
    if content_type is None:
        raise ApiProblem(415, "unsupported_media_type", "Content-Type must be application/json.")
    media_type = content_type.split(";", 1)[0].strip().lower()
    parameters = content_type.split(";", 1)[1].strip().lower() if ";" in content_type else ""
    if media_type != "application/json" or parameters not in {"", "charset=utf-8", "charset=\"utf-8\""}:
        raise ApiProblem(415, "unsupported_media_type", "Content-Type must be application/json.")
    try:
        text = body.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ApiProblem(400, "invalid_json", "The request body is not valid UTF-8 JSON.") from exc
    try:
        decoded = json.loads(
            text,
            object_pairs_hook=_object_pairs,
            parse_constant=_reject_constant,
        )
    except _DuplicateKey as exc:
        raise ApiProblem(
            400,
            "invalid_json",
            "The JSON document contains a duplicate object key.",
            details={"key": str(exc)},
        ) from exc
    except (json.JSONDecodeError, ValueError) as exc:
        raise ApiProblem(400, "invalid_json", "The request body is not valid JSON.") from exc
    validate_json_value(decoded)
    if not isinstance(decoded, dict):
        raise ApiProblem(400, "invalid_json", "The JSON request body must be an object.")
    return decoded


def canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    if not isinstance(value, Mapping):
        raise ConfigurationError("JSON response payload must be an object")
    payload = dict(value)
    validate_json_value(payload)
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ConfigurationError("JSON response payload is invalid") from exc
    return encoded
