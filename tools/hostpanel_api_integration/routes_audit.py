from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from hostpanel_api_http.model import ApiProblem, RequestContext
from hostpanel_api_http.route import Route

from .domain import map_domain_error
from .route_helpers import PAGINATION_PARAMETERS, next_cursor, page_position, query_parameter, require_cursor_int
from .serialization import json_value
from .validation import body_object, integer_field, single_query, text_field

_AUDIT_FILTER_PARAMETERS = (
    query_parameter("actor_kind", {"type": "string"}, "Exact actor-kind filter."),
    query_parameter("actor_id", {"type": "string"}, "Exact actor-ID filter."),
    query_parameter("effective_principal_id", {"type": "string"}, "Exact effective-principal filter."),
    query_parameter("action", {"type": "string"}, "Exact action filter."),
    query_parameter("outcome", {"type": "string", "enum": ["succeeded", "denied", "failed"]}, "Exact outcome filter."),
    query_parameter("request_id", {"type": "string"}, "Exact request-ID filter."),
    query_parameter("reason_code", {"type": "string"}, "Exact reason-code filter."),
    query_parameter("retention_class", {"type": "string", "enum": ["standard", "security", "compliance", "permanent"]}, "Exact retention-class filter."),
    query_parameter("from_at", {"type": "integer", "minimum": 0}, "Inclusive lower timestamp bound."),
    query_parameter("to_at", {"type": "integer", "minimum": 0}, "Inclusive upper timestamp bound."),
)

_EXPORT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["format"],
    "properties": {
        "format": {"type": "string", "enum": ["jsonl", "csv"]},
        "limit": {"type": "integer", "minimum": 1, "maximum": 500},
        "actor_kind": {"type": "string"},
        "actor_id": {"type": "string"},
        "effective_principal_id": {"type": "string"},
        "action": {"type": "string"},
        "outcome": {"type": "string"},
        "request_id": {"type": "string"},
        "reason_code": {"type": "string"},
        "retention_class": {"type": "string"},
        "from_at": {"type": "integer", "minimum": 0},
        "to_at": {"type": "integer", "minimum": 0},
    },
}


def register_audit_routes(api) -> None:
    api.register(Route(
        method="GET", path="/api/v1/tenants/{tenant_id}/audit-events",
        operation_id="searchTenantAuditEvents", summary="Search tenant audit events",
        tags=("Audit",), required_scope="audit:read", resolve_target=api.resolvers.tenant,
        query_parameters=PAGINATION_PARAMETERS + _AUDIT_FILTER_PARAMETERS,
        response_schema={"type": "object"}, handler=api.search_audit_events,
    ))
    api.register(Route(
        method="GET", path="/api/v1/tenants/{tenant_id}/audit-events/{event_id}",
        operation_id="getTenantAuditEvent", summary="Read one tenant audit event",
        tags=("Audit",), required_scope="audit:read", resolve_target=api.resolvers.audit_event,
        response_schema={"type": "object"}, handler=api.get_audit_event,
    ))
    api.register(Route(
        method="POST", path="/api/v1/tenants/{tenant_id}/audit-exports",
        operation_id="exportTenantAuditEvents", summary="Create a bounded inline tenant audit export",
        tags=("Audit",), required_scope="audit:export", resolve_target=api.resolvers.tenant,
        expects_json_body=True, idempotency_required=True, request_schema=_EXPORT_SCHEMA,
        response_schema={"type": "object"}, success_status=201, handler=api.export_audit_events,
    ))


class AuditRouteHandlers:
    @staticmethod
    def _filter_values_from_query(context: RequestContext) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name in (
            "actor_kind", "actor_id", "effective_principal_id", "action", "outcome",
            "request_id", "reason_code", "retention_class",
        ):
            value = single_query(context, name)
            if value is not None:
                result[name] = value
        for name in ("from_at", "to_at"):
            value = single_query(context, name)
            if value is not None:
                if not value.isascii() or not value.isdecimal():
                    raise ApiProblem(400, "invalid_query", f"Query parameter '{name}' is invalid.")
                result[name] = int(value)
        return result

    @staticmethod
    def _filter_values_from_body(data: Mapping[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name in (
            "actor_kind", "actor_id", "effective_principal_id", "action", "outcome",
            "request_id", "reason_code", "retention_class",
        ):
            if name in data:
                result[name] = text_field(data, name)
        for name in ("from_at", "to_at"):
            if name in data:
                result[name] = integer_field(data, name, minimum=0)
        return result

    def search_audit_events(self, context: RequestContext):
        position, limit = page_position(context, self.cursor_codec, operation="searchTenantAuditEvents")
        after = None
        if position is not None:
            after = (
                require_cursor_int(position, "occurred_at"),
                require_cursor_int(position, "sequence", minimum=1),
            )
        filters = self._filter_values_from_query(context)
        try:
            rows = self.stores.audit.search_events(
                tenant_id=context.target.tenant_id, after=after, limit=limit + 1, **filters,
            )
        except Exception as exc:
            raise map_domain_error(exc) from exc
        has_more = len(rows) > limit
        items = rows[:limit]
        cursor = None
        if has_more and items:
            last = items[-1]
            cursor = next_cursor(
                context, self.cursor_codec, operation="searchTenantAuditEvents",
                position={"occurred_at": last.occurred_at, "sequence": last.sequence},
            )
        return {"events": json_value(items), "next_cursor": cursor}

    def get_audit_event(self, context: RequestContext):
        try:
            event = self.stores.audit.get_event(
                context.path_parameters["event_id"], tenant_id=context.target.tenant_id,
            )
            return {"event": json_value(event)}
        except Exception as exc:
            raise map_domain_error(exc, missing_is_not_found=True) from exc

    def export_audit_events(self, context: RequestContext):
        allowed = {
            "format", "limit", "actor_kind", "actor_id", "effective_principal_id",
            "action", "outcome", "request_id", "reason_code", "retention_class",
            "from_at", "to_at",
        }
        data = body_object(context, allowed=allowed, required={"format"})
        export_format = text_field(data, "format")
        if export_format not in {"jsonl", "csv"}:
            raise ApiProblem(400, "invalid_field", "Field 'format' must be 'jsonl' or 'csv'.")
        limit = integer_field(data, "limit", required=False, minimum=1, maximum=500) or 100
        filters = self._filter_values_from_body(data)

        def mutation():
            try:
                events = self.stores.audit.search_events(
                    tenant_id=context.target.tenant_id, limit=limit, **filters,
                )
                payload = (
                    self.stores.audit.export_jsonl(events)
                    if export_format == "jsonl"
                    else self.stores.audit.export_csv(events)
                )
                if len(payload) > 512 * 1024:
                    raise ApiProblem(413, "export_too_large", "The audit export exceeds the inline response limit.")
                digest = self.stores.audit.export_digest(payload)
                audit_event = self.audit_writer.append(
                    context, action="audit.exported", target_kind="audit_export", target_id=digest[:32],
                    metadata={"format": export_format, "event_count": len(events), "sha256": digest},
                    retention_class="compliance",
                )
                return {
                    "format": export_format,
                    "event_count": len(events),
                    "sha256": digest,
                    "content": payload.decode("utf-8"),
                    "audit_event_id": audit_event.event_id,
                }
            except ApiProblem:
                raise
            except Exception as exc:
                raise map_domain_error(exc) from exc
        return self.idempotency.execute(context, status=201, callback=mutation)
