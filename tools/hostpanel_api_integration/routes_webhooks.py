from __future__ import annotations

import secrets

from hostpanel_api_http.model import RateLimitPolicy, RequestContext
from hostpanel_api_http.route import Route

from .domain import map_domain_error
from .serialization import json_value
from .validation import body_object, integer_field, string_list_field, text_field


_DESTINATION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["url", "secret_reference", "event_types"],
    "properties": {
        "url": {"type": "string", "format": "uri", "maxLength": 2048},
        "secret_reference": {"type": "string", "minLength": 1, "maxLength": 256},
        "event_types": {"type": "array", "minItems": 1, "maxItems": 100, "items": {"type": "string"}},
        "expected_version": {"type": "integer", "minimum": 1},
    },
}


def _destination_payload(destination):
    payload = json_value(destination)
    if not isinstance(payload, dict):
        raise TypeError("webhook destination serialization is invalid")
    payload.pop("secret_reference", None)
    payload.pop("secret_ref", None)
    payload["secret_configured"] = True
    return payload


def register_webhook_routes(api) -> None:
    api.register(Route(
        method="POST", path="/api/v1/tenants/{tenant_id}/webhook-destinations",
        operation_id="createWebhookDestination", summary="Create a tenant webhook destination",
        tags=("Webhooks",), required_scope="webhook:create", resolve_target=api.resolvers.tenant,
        expects_json_body=True, idempotency_required=True, request_schema=_DESTINATION_SCHEMA,
        response_schema={"type": "object"}, success_status=201,
        source_rate_limit=RateLimitPolicy(20, 60), principal_rate_limit=RateLimitPolicy(20, 60),
        handler=api.create_webhook_destination,
    ))
    api.register(Route(
        method="GET", path="/api/v1/tenants/{tenant_id}/webhook-destinations/{destination_id}",
        operation_id="getWebhookDestination", summary="Read a tenant webhook destination",
        tags=("Webhooks",), required_scope="webhook:read", resolve_target=api.resolvers.destination,
        response_schema={"type": "object"}, handler=api.get_webhook_destination,
    ))
    api.register(Route(
        method="PUT", path="/api/v1/tenants/{tenant_id}/webhook-destinations/{destination_id}",
        operation_id="updateWebhookDestination", summary="Replace a tenant webhook destination",
        tags=("Webhooks",), required_scope="webhook:update", resolve_target=api.resolvers.destination,
        expects_json_body=True, idempotency_required=True,
        request_schema={**_DESTINATION_SCHEMA, "required": ["url", "secret_reference", "event_types", "expected_version"]},
        response_schema={"type": "object"}, handler=api.update_webhook_destination,
    ))
    api.register(Route(
        method="DELETE", path="/api/v1/tenants/{tenant_id}/webhook-destinations/{destination_id}",
        operation_id="disableWebhookDestination", summary="Disable a tenant webhook destination",
        tags=("Webhooks",), required_scope="webhook:disable", resolve_target=api.resolvers.destination,
        expects_json_body=True, idempotency_required=True,
        request_schema={"type": "object", "additionalProperties": False, "required": ["expected_version"], "properties": {"expected_version": {"type": "integer", "minimum": 1}}},
        response_schema={"type": "object"}, handler=api.disable_webhook_destination,
    ))


class WebhookRouteHandlers:
    def create_webhook_destination(self, context: RequestContext):
        data = body_object(context, allowed={"url", "secret_reference", "event_types"}, required={"url", "secret_reference", "event_types"})
        url = text_field(data, "url", maximum=2048)
        secret_reference = text_field(data, "secret_reference", maximum=256)
        event_types = string_list_field(data, "event_types", maximum=100)
        tenant = context.target.tenant_id

        def mutation():
            destination_id = "whd_" + secrets.token_urlsafe(16)
            try:
                destination = self.stores.webhooks.create_destination(
                    destination_id=destination_id, tenant_id=tenant, url=url,
                    secret_reference=secret_reference, event_types=event_types, now=context.now,
                )
                event = self.audit_writer.append(
                    context, action="webhook_destination.created", target_kind="webhook_destination",
                    target_id=destination_id,
                    metadata={"event_type_count": len(event_types), "secret_reference_changed": True},
                    retention_class="security",
                )
                return {"destination": _destination_payload(destination), "audit_event_id": event.event_id}
            except Exception as exc:
                raise map_domain_error(exc) from exc
        return self.idempotency.execute(context, status=201, callback=mutation)

    def get_webhook_destination(self, context: RequestContext):
        try:
            destination = self.stores.webhooks.get_destination(
                context.path_parameters["destination_id"], tenant_id=context.target.tenant_id,
            )
            return {"destination": _destination_payload(destination)}
        except Exception as exc:
            raise map_domain_error(exc, missing_is_not_found=True) from exc

    def update_webhook_destination(self, context: RequestContext):
        data = body_object(context, allowed={"url", "secret_reference", "event_types", "expected_version"}, required={"url", "secret_reference", "event_types", "expected_version"})
        url = text_field(data, "url", maximum=2048)
        secret_reference = text_field(data, "secret_reference", maximum=256)
        event_types = string_list_field(data, "event_types", maximum=100)
        version = integer_field(data, "expected_version", minimum=1)
        destination_id = context.path_parameters["destination_id"]

        def mutation():
            try:
                destination = self.stores.webhooks.update_destination(
                    destination_id, tenant_id=context.target.tenant_id, expected_version=version,
                    url=url, secret_reference=secret_reference, event_types=event_types, now=context.now,
                )
                event = self.audit_writer.append(
                    context, action="webhook_destination.updated", target_kind="webhook_destination",
                    target_id=destination_id,
                    metadata={"version": destination.version, "event_type_count": len(event_types), "secret_reference_changed": True},
                    retention_class="security",
                )
                return {"destination": _destination_payload(destination), "audit_event_id": event.event_id}
            except Exception as exc:
                raise map_domain_error(exc) from exc
        return self.idempotency.execute(context, status=200, callback=mutation)

    def disable_webhook_destination(self, context: RequestContext):
        data = body_object(context, allowed={"expected_version"}, required={"expected_version"})
        version = integer_field(data, "expected_version", minimum=1)
        destination_id = context.path_parameters["destination_id"]

        def mutation():
            try:
                destination = self.stores.webhooks.disable_destination(
                    destination_id, tenant_id=context.target.tenant_id,
                    expected_version=version, now=context.now,
                )
                event = self.audit_writer.append(
                    context, action="webhook_destination.disabled", target_kind="webhook_destination",
                    target_id=destination_id, metadata={"version": destination.version},
                    retention_class="security",
                )
                return {"destination": _destination_payload(destination), "audit_event_id": event.event_id}
            except Exception as exc:
                raise map_domain_error(exc) from exc
        return self.idempotency.execute(context, status=200, callback=mutation)
