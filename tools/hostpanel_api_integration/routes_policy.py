from __future__ import annotations

from hostpanel_api_http.model import RequestContext
from hostpanel_api_http.route import Route

from .domain import map_domain_error
from .serialization import json_value
from .validation import body_object, text_field


def register_policy_routes(api) -> None:
    api.register(Route(
        method="POST", path="/api/v1/tenants/{tenant_id}/policy-simulations",
        operation_id="simulateTenantPolicy", summary="Simulate a tenant capability decision",
        tags=("Policy",), required_scope="policy:simulate", resolve_target=api.resolvers.tenant,
        expects_json_body=True,
        request_schema={
            "type": "object", "additionalProperties": False, "required": ["capability"],
            "properties": {
                "principal_id": {"type": "string"},
                "capability": {"type": "string"},
                "resource_type": {"type": "string"},
                "resource_id": {"type": "string"},
            },
        },
        response_schema={"type": "object"}, handler=api.simulate_policy,
    ))


class PolicyRouteHandlers:
    def simulate_policy(self, context: RequestContext):
        data = body_object(
            context,
            allowed={"principal_id", "capability", "resource_type", "resource_id"},
            required={"capability"},
        )
        principal_id = text_field(data, "principal_id", required=False) or context.principal.principal_id
        capability = text_field(data, "capability")
        resource_type = text_field(data, "resource_type", required=False)
        resource_id = text_field(data, "resource_id", required=False)
        if (resource_type is None) != (resource_id is None):
            from hostpanel_api_http.model import ApiProblem
            raise ApiProblem(400, "invalid_field", "Resource type and resource ID must be supplied together.")
        try:
            with self.stores.atomic():
                decision = self.stores.rbac.simulate(
                    principal_id=principal_id, capability=capability,
                    tenant_id=context.target.tenant_id,
                    resource_type=resource_type, resource_id=resource_id,
                    now=context.now,
                )
                event = self.audit_writer.append(
                    context, action="policy.simulated",
                    outcome="succeeded" if decision.allowed else "denied",
                    target_kind="principal", target_id=principal_id,
                    metadata={
                        "capability": capability,
                        "resource_type": resource_type,
                        "resource_id": resource_id,
                        "decision_reason": decision.reason,
                    },
                    reason_code=None if decision.allowed else decision.reason,
                    retention_class="security",
                )
                return {"decision": json_value(decision), "audit_event_id": event.event_id}
        except Exception as exc:
            if exc.__class__.__name__ == "ApiProblem":
                raise
            raise map_domain_error(exc) from exc
