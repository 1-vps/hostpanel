from __future__ import annotations

from hostpanel_api_http.model import ApiProblem, RateLimitPolicy, RequestContext
from hostpanel_api_http.route import Route

from .domain import map_domain_error
from .route_helpers import PAGINATION_PARAMETERS, next_cursor, page_position, require_cursor_int, require_cursor_text
from .serialization import json_value
from .validation import body_object, integer_field, object_field, single_query, text_field


_JOB_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["job_type", "payload"],
    "properties": {
        "job_type": {"type": "string", "minLength": 1, "maxLength": 128},
        "payload": {"type": "object"},
        "dedupe_key": {"type": "string", "minLength": 8, "maxLength": 128},
        "priority": {"type": "integer", "minimum": 0, "maximum": 100},
        "max_attempts": {"type": "integer", "minimum": 1, "maximum": 100},
    },
}


def register_job_routes(api) -> None:
    api.register(Route(
        method="POST", path="/api/v1/tenants/{tenant_id}/jobs",
        operation_id="createTenantJob", summary="Create a durable tenant job",
        tags=("Jobs",), required_scope="job:create", resolve_target=api.resolvers.tenant,
        expects_json_body=True, idempotency_required=True, request_schema=_JOB_SCHEMA,
        response_schema={"type": "object"}, success_status=201,
        source_rate_limit=RateLimitPolicy(60, 60), principal_rate_limit=RateLimitPolicy(60, 60),
        handler=api.create_job,
    ))
    api.register(Route(
        method="GET", path="/api/v1/tenants/{tenant_id}/jobs",
        operation_id="listTenantJobs", summary="List durable tenant jobs",
        tags=("Jobs",), required_scope="job:read", resolve_target=api.resolvers.tenant,
        query_parameters=PAGINATION_PARAMETERS + (
            {"in": "query", "name": "status", "required": False, "schema": {"type": "string", "enum": ["pending", "running", "retry_wait", "succeeded", "failed", "cancelled"]}, "description": "Optional exact job status filter."},
        ),
        response_schema={"type": "object"}, handler=api.list_jobs,
    ))
    api.register(Route(
        method="GET", path="/api/v1/tenants/{tenant_id}/jobs/{job_id}",
        operation_id="getTenantJob", summary="Read one durable tenant job",
        tags=("Jobs",), required_scope="job:read", resolve_target=api.resolvers.job,
        response_schema={"type": "object"}, handler=api.get_job,
    ))
    api.register(Route(
        method="POST", path="/api/v1/tenants/{tenant_id}/jobs/{job_id}/cancel",
        operation_id="cancelTenantJob", summary="Request durable job cancellation",
        tags=("Jobs",), required_scope="job:cancel", resolve_target=api.resolvers.job,
        idempotency_required=True, response_schema={"type": "object"}, handler=api.cancel_job,
    ))
    api.register(Route(
        method="GET", path="/api/v1/tenants/{tenant_id}/jobs/{job_id}/logs",
        operation_id="listTenantJobLogs", summary="List bounded durable job logs",
        tags=("Jobs",), required_scope="job:read", resolve_target=api.resolvers.job,
        query_parameters=PAGINATION_PARAMETERS, response_schema={"type": "object"}, handler=api.list_job_logs,
    ))


class JobRouteHandlers:
    def create_job(self, context: RequestContext):
        data = body_object(context, allowed={"job_type", "payload", "dedupe_key", "priority", "max_attempts"}, required={"job_type", "payload"})
        job_type = text_field(data, "job_type")
        if job_type not in self.allowed_job_types:
            raise ApiProblem(400, "unsupported_job_type", "The requested job type is not registered.")
        payload = object_field(data, "payload")
        dedupe = text_field(data, "dedupe_key", required=False)
        priority = integer_field(data, "priority", required=False, minimum=0, maximum=100)
        max_attempts = integer_field(data, "max_attempts", required=False, minimum=1, maximum=100)
        tenant = context.target.tenant_id

        def mutation():
            try:
                creation = self.stores.control.create_job(
                    tenant_id=tenant, job_type=job_type, payload=dict(payload), dedupe_key=dedupe,
                    priority=50 if priority is None else priority,
                    max_attempts=3 if max_attempts is None else max_attempts,
                    now=context.now,
                )
                event = self.audit_writer.append(
                    context, action="job.created", target_kind="job", target_id=creation.job.job_id,
                    metadata={"job_type": job_type, "created": creation.created, "dedupe_key_present": dedupe is not None},
                )
                return {"job": json_value(creation.job), "created": creation.created, "audit_event_id": event.event_id}
            except Exception as exc:
                raise map_domain_error(exc) from exc
        return self.idempotency.execute(context, status=201, callback=mutation)

    def list_jobs(self, context: RequestContext):
        position, limit = page_position(context, self.cursor_codec, operation="listTenantJobs")
        after = None
        if position is not None:
            after = (require_cursor_int(position, "created_at"), require_cursor_text(position, "job_id"))
        status = single_query(context, "status")
        try:
            rows = self.stores.control.list_jobs(
                tenant_id=context.target.tenant_id, status=status, after=after, limit=limit + 1,
            )
        except Exception as exc:
            raise map_domain_error(exc) from exc
        has_more = len(rows) > limit
        items = rows[:limit]
        cursor = None
        if has_more and items:
            last = items[-1]
            cursor = next_cursor(context, self.cursor_codec, operation="listTenantJobs", position={"created_at": last.created_at, "job_id": last.job_id})
        return {"jobs": json_value(items), "next_cursor": cursor}

    def get_job(self, context: RequestContext):
        try:
            return {
                "job": json_value(
                    self.stores.control.get_job(context.path_parameters["job_id"])
                )
            }
        except Exception as exc:
            raise map_domain_error(exc, missing_is_not_found=True) from exc

    def cancel_job(self, context: RequestContext):
        job_id = context.path_parameters["job_id"]

        def mutation():
            try:
                job = self.stores.control.request_job_cancel(job_id, now=context.now)
                event = self.audit_writer.append(
                    context, action="job.cancel_requested", target_kind="job", target_id=job_id,
                    metadata={"status": job.status, "cancel_requested": job.cancel_requested},
                )
                return {"job": json_value(job), "audit_event_id": event.event_id}
            except Exception as exc:
                raise map_domain_error(exc) from exc
        return self.idempotency.execute(context, status=200, callback=mutation)

    def list_job_logs(self, context: RequestContext):
        position, limit = page_position(context, self.cursor_codec, operation="listTenantJobLogs")
        after_sequence = 0
        if position is not None:
            after_sequence = require_cursor_int(position, "sequence")
        try:
            rows = self.stores.control.list_job_logs(
                context.path_parameters["job_id"], after_sequence=after_sequence, limit=limit + 1,
            )
        except Exception as exc:
            raise map_domain_error(exc) from exc
        has_more = len(rows) > limit
        items = rows[:limit]
        cursor = None
        if has_more and items:
            cursor = next_cursor(context, self.cursor_codec, operation="listTenantJobLogs", position={"sequence": items[-1].sequence})
        return {"logs": json_value(items), "next_cursor": cursor}
