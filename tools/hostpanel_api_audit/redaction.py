from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from .model import MAX_METADATA_BYTES, MAX_METADATA_DEPTH, MAX_METADATA_NODES, ValidationError

REDACTED = "[REDACTED]"
_SENSITIVE = re.compile(
    r"(?:authorization|cookie|credential|password|passwd|secret|token|api[_-]?key|private[_-]?key|signature|session)",
    re.IGNORECASE,
)


def _sensitive_value(value: str) -> bool:
    lowered = value.lstrip().lower()
    return lowered.startswith("bearer ") or "-----begin private key-----" in lowered


def redact_metadata(value: Mapping[str, Any] | None) -> tuple[dict[str, Any], str]:
    source: Mapping[str, Any] = {} if value is None else value
    if not isinstance(source, Mapping):
        raise ValidationError("audit metadata must be an object")
    remaining = [MAX_METADATA_NODES]

    def visit(node: Any, depth: int, sensitive: bool = False) -> Any:
        if depth > MAX_METADATA_DEPTH:
            raise ValidationError("audit metadata is too deeply nested")
        remaining[0] -= 1
        if remaining[0] < 0:
            raise ValidationError("audit metadata contains too many values")
        if sensitive:
            return REDACTED
        if node is None or isinstance(node, bool):
            return node
        if isinstance(node, int) and not isinstance(node, bool):
            if not -(2**63) <= node <= 2**63 - 1:
                raise ValidationError("audit metadata contains an out-of-range integer")
            return node
        if isinstance(node, float):
            raise ValidationError("audit metadata does not permit floating-point values")
        if isinstance(node, str):
            if len(node) > 4096 or any(ord(ch) == 0 for ch in node):
                raise ValidationError("audit metadata contains invalid text")
            return REDACTED if _sensitive_value(node) else node
        if isinstance(node, Mapping):
            result: dict[str, Any] = {}
            for raw_key, child in node.items():
                if not isinstance(raw_key, str) or not raw_key or len(raw_key) > 128:
                    raise ValidationError("audit metadata contains an invalid object key")
                if any(ord(ch) < 32 or ord(ch) == 127 for ch in raw_key):
                    raise ValidationError("audit metadata contains an invalid object key")
                result[raw_key] = visit(child, depth + 1, _SENSITIVE.search(raw_key) is not None)
            return result
        if isinstance(node, (list, tuple)):
            return [visit(child, depth + 1) for child in node]
        raise ValidationError("audit metadata is not JSON serializable")

    redacted = visit(source, 0)
    encoded = json.dumps(redacted, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    if len(encoded.encode("utf-8")) > MAX_METADATA_BYTES:
        raise ValidationError("audit metadata is too large")
    return redacted, encoded


def decode_metadata(value: str) -> dict[str, Any]:
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError("persisted audit metadata is invalid") from exc
    if not isinstance(decoded, dict):
        raise ValidationError("persisted audit metadata must be an object")
    redacted, canonical = redact_metadata(decoded)
    if canonical != value:
        raise ValidationError("persisted audit metadata is not canonical or redacted")
    return redacted
