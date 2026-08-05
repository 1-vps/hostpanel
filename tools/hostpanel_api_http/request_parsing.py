from __future__ import annotations

import re
import urllib.parse
from collections import defaultdict
from collections.abc import Mapping

from .model import ApiProblem, RateLimitDecision

_PERCENT_RE = re.compile(r"%(?![0-9A-Fa-f]{2})")

def _normalize_path(raw_path: str) -> str:
    if "?" in raw_path or "#" in raw_path or "\\" in raw_path or "\x00" in raw_path:
        raise ApiProblem(400, "invalid_path", "The request path is invalid.")
    if _PERCENT_RE.search(raw_path) is not None:
        raise ApiProblem(400, "invalid_path", "The request path is invalid.")
    lowered = raw_path.lower()
    if "%2f" in lowered or "%5c" in lowered:
        raise ApiProblem(400, "invalid_path", "The request path is invalid.")
    if not raw_path.startswith("/") or raw_path.endswith("/") or "//" in raw_path:
        raise ApiProblem(400, "invalid_path", "The request path is invalid.")
    decoded_segments: list[str] = []
    for raw_segment in raw_path.split("/")[1:]:
        try:
            segment = urllib.parse.unquote_to_bytes(raw_segment).decode("utf-8", "strict")
        except UnicodeDecodeError as exc:
            raise ApiProblem(400, "invalid_path", "The request path is invalid.") from exc
        if segment in {"", ".", ".."} or any(
            ord(character) < 32 or ord(character) == 127 for character in segment
        ):
            raise ApiProblem(400, "invalid_path", "The request path is invalid.")
        decoded_segments.append(segment)
    return "/" + "/".join(decoded_segments)


def _parse_query(query_string: str) -> Mapping[str, tuple[str, ...]]:
    if _PERCENT_RE.search(query_string) is not None:
        raise ApiProblem(400, "invalid_query", "The request query is invalid.")
    try:
        pairs = urllib.parse.parse_qsl(
            query_string,
            keep_blank_values=True,
            strict_parsing=False,
            max_num_fields=100,
            separator="&",
            encoding="utf-8",
            errors="strict",
        )
    except (UnicodeDecodeError, ValueError) as exc:
        raise ApiProblem(400, "invalid_query", "The request query is invalid.") from exc
    values: defaultdict[str, list[str]] = defaultdict(list)
    for key, value in pairs:
        if (
            not key
            or len(key.encode("utf-8")) > 128
            or len(value.encode("utf-8")) > 2048
            or any(ord(character) < 32 or ord(character) == 127 for character in key + value)
        ):
            raise ApiProblem(400, "invalid_query", "The request query is invalid.")
        values[key].append(value)
    return {key: tuple(items) for key, items in values.items()}


def _rate_limit_problem(decision: RateLimitDecision) -> ApiProblem:
    return ApiProblem(
        429,
        "rate_limited",
        "The request rate limit has been exceeded.",
        headers={"Retry-After": str(decision.retry_after)},
    )
