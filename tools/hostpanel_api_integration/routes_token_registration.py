from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from hostpanel_api_http.model import ApiProblem, RateLimitPolicy, RequestContext
from hostpanel_api_http.route import Route

from .domain import map_domain_error
from .route_helpers import PAGINATION_PARAMETERS, next_cursor, page_position, require_cursor_int, require_cursor_text
from .serialization import envelope, json_value
from .validation import body_object, integer_field, object_field, string_list_field, text_field


_ACCOUNT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name", "scopes"],
    "properties": {
        "name": {"type": "string", "minLength": 1, "maxLength": 128},
        "scopes": {"type": "array", "minItems": 1, "maxItems": 128, "items": {"type": "string"}},
        "source_cidrs": {"type": "array", "maxItems": 64, "items": {"type": "string"}},
        "expires_at": {"type": "integer", "minimum": 0},
    },
}

_TOKEN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["expires_at"],
    "properties": {
        "expires_at": {"type": "integer", "minimum": 0},
        "label": {"type": "string", "maxLength": 128},
        "scopes": {"type": "array", "minItems": 1, "maxItems": 128, "items": {"type": "string"}},
        "source_cidrs": {"type": "array", "maxItems": 64, "items": {"type": "string"}},
        "resources": {"type": "object"},
    },
}


def register_token_routes(api) -> None:
    api.register(Route(
        method="POST", path="/api/v1/tenants/{tenant_id}/service-accounts",
        operation_id="createServiceAccount", summary="Create a tenant service account and RBAC binding",
        tags=("Service accounts",), required_scope="service_account:create",
        resolve_target=api.resolvers.tenant, expects_json_body=True, idempotency_required=True,
        request_schema=_ACCOUNT_SCHEMA, response_schema={"type": "object"}, success_status=201,
        source_rate_limit=RateLimitPolicy(30, 60), principal_rate_limit=RateLimitPolicy(30, 60),
        handler=api.create_service_account,
    ))
    api.register(Route(
        method="GET", path="/api/v1/tenants/{tenant_id}/service-accounts",
        operation_id="listServiceAccounts", summary="List tenant service accounts",
        tags=("Service accounts",), required_scope="service_account:read",
        resolve_target=api.resolvers.tenant, query_parameters=PAGINATION_PARAMETERS,
        response_schema={"type": "object"}, handler=api.list_service_accounts,
    ))
    api.register(Route(
        method="GET", path="/api/v1/tenants/{tenant_id}/service-accounts/{account_id}",
        operation_id="getServiceAccount", summary="Read one tenant service account",
        tags=("Service accounts",), required_scope="service_account:read",
        resolve_target=api.resolvers.service_account, response_schema={"type": "object"},
        handler=api.get_service_account,
    ))
    api.register(Route(
        method="PATCH", path="/api/v1/tenants/{tenant_id}/service-accounts/{account_id}",
        operation_id="updateServiceAccountState", summary="Enable or disable a tenant service account",
        tags=("Service accounts",), required_scope="service_account:update",
        resolve_target=api.resolvers.service_account, expects_json_body=True, idempotency_required=True,
        request_schema={"type": "object", "additionalProperties": False, "required": ["enabled"], "properties": {"enabled": {"type": "boolean"}}},
        response_schema={"type": "object"}, handler=api.set_service_account_state,
    ))
    api.register(Route(
        method="POST", path="/api/v1/tenants/{tenant_id}/service-accounts/{account_id}/tokens",
        operation_id="issueServiceAccountToken", summary="Issue a one-time tenant API token",
        tags=("API tokens",), required_scope="token:issue", resolve_target=api.resolvers.service_account,
        expects_json_body=True, idempotency_required=True, request_schema=_TOKEN_SCHEMA,
        response_schema={"type": "object"}, success_status=201, handler=api.issue_token,
    ))
    api.register(Route(
        method="GET", path="/api/v1/tenants/{tenant_id}/service-accounts/{account_id}/tokens",
        operation_id="listServiceAccountTokens", summary="List tenant API-token metadata",
        tags=("API tokens",), required_scope="token:read", resolve_target=api.resolvers.service_account,
        query_parameters=PAGINATION_PARAMETERS, response_schema={"type": "object"}, handler=api.list_tokens,
    ))
    api.register(Route(
        method="DELETE", path="/api/v1/tenants/{tenant_id}/service-accounts/{account_id}/tokens/{token_id}",
        operation_id="revokeServiceAccountToken", summary="Revoke a tenant API token",
        tags=("API tokens",), required_scope="token:revoke", resolve_target=api.resolvers.token,
        idempotency_required=True, response_schema={"type": "object"}, handler=api.revoke_token,
    ))
