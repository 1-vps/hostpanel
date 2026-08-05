from __future__ import annotations

import dataclasses
from collections.abc import Mapping

from .model import ApiProblem, ConfigurationError


@dataclasses.dataclass(frozen=True, slots=True)
class PageRequest:
    cursor: str | None
    limit: int


def parse_page_query(
    query: Mapping[str, tuple[str, ...]],
    *,
    default_limit: int = 50,
    max_limit: int = 100,
) -> PageRequest:
    if not isinstance(query, Mapping):
        raise TypeError("query must be a mapping")
    if (
        not isinstance(default_limit, int)
        or isinstance(default_limit, bool)
        or not isinstance(max_limit, int)
        or isinstance(max_limit, bool)
        or not 1 <= default_limit <= max_limit <= 1000
    ):
        raise ConfigurationError("pagination limits are invalid")
    cursor_values = query.get("cursor", ())
    limit_values = query.get("limit", ())
    if len(cursor_values) > 1 or len(limit_values) > 1:
        raise ApiProblem(
            400,
            "invalid_pagination",
            "Pagination parameters must not be repeated.",
        )
    cursor = None
    if cursor_values:
        cursor = cursor_values[0]
        if not cursor or len(cursor.encode("utf-8")) > 4096:
            raise ApiProblem(400, "invalid_cursor", "The pagination cursor is invalid.")
    limit = default_limit
    if limit_values:
        raw_limit = limit_values[0]
        if not raw_limit.isascii() or not raw_limit.isdecimal() or raw_limit.startswith("0"):
            raise ApiProblem(400, "invalid_pagination", "The pagination limit is invalid.")
        limit = int(raw_limit)
        if not 1 <= limit <= max_limit:
            raise ApiProblem(400, "invalid_pagination", "The pagination limit is invalid.")
    return PageRequest(cursor, limit)
