from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from hostpanel_api_http.cursor import CursorCodec
from hostpanel_api_http.model import ApiProblem, RequestContext
from hostpanel_api_http.pagination import parse_page_query


def query_parameter(name: str, schema: Mapping[str, Any], description: str, *, required: bool = False) -> Mapping[str, Any]:
    return {
        "in": "query",
        "name": name,
        "required": required,
        "schema": dict(schema),
        "description": description,
    }


PAGINATION_PARAMETERS = (
    query_parameter("cursor", {"type": "string", "maxLength": 4096}, "Opaque signed pagination cursor."),
    query_parameter("limit", {"type": "integer", "minimum": 1, "maximum": 100}, "Maximum number of records."),
)


def page_position(
    context: RequestContext,
    codec: CursorCodec,
    *,
    operation: str,
    max_limit: int = 100,
) -> tuple[Mapping[str, Any] | None, int]:
    if context.principal is None or context.target is None:
        raise RuntimeError("pagination requires an authenticated target")
    page = parse_page_query(context.query_parameters, default_limit=min(50, max_limit), max_limit=max_limit)
    position = None
    if page.cursor is not None:
        position = codec.decode(
            page.cursor,
            operation=operation,
            principal_id=context.principal.principal_id,
            tenant_id=context.target.tenant_id,
            now=context.now,
        )
    return position, page.limit


def next_cursor(
    context: RequestContext,
    codec: CursorCodec,
    *,
    operation: str,
    position: Mapping[str, Any] | None,
) -> str | None:
    if position is None:
        return None
    if context.principal is None or context.target is None:
        raise RuntimeError("pagination requires an authenticated target")
    return codec.encode(
        position,
        operation=operation,
        principal_id=context.principal.principal_id,
        tenant_id=context.target.tenant_id,
        now=context.now,
    )


def require_cursor_int(position: Mapping[str, Any], name: str, *, minimum: int = 0) -> int:
    value = position.get(name)
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ApiProblem(400, "invalid_cursor", "The pagination cursor is invalid.")
    return value


def require_cursor_text(position: Mapping[str, Any], name: str) -> str:
    value = position.get(name)
    if not isinstance(value, str) or not value:
        raise ApiProblem(400, "invalid_cursor", "The pagination cursor is invalid.")
    return value
