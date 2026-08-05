from __future__ import annotations

from hostpanel_api_http.model import ApiProblem, RequestContext

from .domain import map_domain_error
from .route_helpers import next_cursor, page_position, require_cursor_int, require_cursor_text
from .serialization import json_value
from .validation import body_object, integer_field, object_field, string_list_field, text_field


class TokenCredentialRouteHandlers:
    def issue_token(self, context: RequestContext):
        data = body_object(context, allowed={"expires_at", "label", "scopes", "source_cidrs", "resources"}, required={"expires_at"})
        expires = integer_field(data, "expires_at")
        label = text_field(data, "label", required=False) or "API token"
        scopes = string_list_field(data, "scopes", required=False)
        cidrs = string_list_field(data, "source_cidrs", required=False)
        resources = object_field(data, "resources", required=False)
        normalized_resources = None
        if resources is not None:
            normalized_resources = {}
            for key, values in resources.items():
                if not isinstance(key, str) or not isinstance(values, list) or any(not isinstance(item, str) for item in values):
                    raise ApiProblem(400, "invalid_field", "Field 'resources' is invalid.")
                normalized_resources[key] = tuple(values)
        account_id = context.path_parameters["account_id"]
        tenant = context.target.tenant_id

        def mutation():
            try:
                issued = self.stores.tokens.issue_token(
                    account_id, expires_at=expires, actor=self._token_actor(context), label=label,
                    scopes=scopes, tenant_ids=(tenant,), allow_all_tenants=False,
                    source_cidrs=cidrs, resources=normalized_resources, now=context.now,
                )
                event = self.audit_writer.append(
                    context, action="token.issued", target_kind="api_token",
                    target_id=issued.metadata.token_id,
                    metadata={"account_id": account_id, "expires_at": expires, "scope_count": len(issued.metadata.scopes)},
                    retention_class="security",
                )
                replay = {
                    "metadata": json_value(issued.metadata),
                    "secret_available": False,
                    "audit_event_id": event.event_id,
                }
                first = {
                    **replay,
                    "token": issued.token,
                    "secret_available": True,
                }
                return first, replay
            except Exception as exc:
                raise map_domain_error(exc) from exc
        return self.idempotency.execute_one_time_secret(
            context,
            status=201,
            callback=mutation,
        )

    def list_tokens(self, context: RequestContext):
        position, limit = page_position(context, self.cursor_codec, operation="listServiceAccountTokens")
        after = None
        if position is not None:
            after = (require_cursor_int(position, "created_at"), require_cursor_text(position, "token_id"))
        try:
            rows = self.stores.tokens.list_tokens(context.path_parameters["account_id"], after=after, limit=limit + 1)
        except Exception as exc:
            raise map_domain_error(exc) from exc
        has_more = len(rows) > limit
        items = rows[:limit]
        cursor = None
        if has_more and items:
            last = items[-1]
            cursor = next_cursor(context, self.cursor_codec, operation="listServiceAccountTokens", position={"created_at": last.created_at, "token_id": last.token_id})
        return {"tokens": json_value(items), "next_cursor": cursor}

    def revoke_token(self, context: RequestContext):
        token_id = context.path_parameters["token_id"]

        def mutation():
            try:
                self.stores.tokens.revoke_token(token_id, actor=self._token_actor(context), now=context.now)
                event = self.audit_writer.append(
                    context, action="token.revoked", target_kind="api_token", target_id=token_id,
                    metadata={"account_id": context.path_parameters["account_id"]}, retention_class="security",
                )
                return {"revoked": True, "token_id": token_id, "audit_event_id": event.event_id}
            except Exception as exc:
                # A completed idempotent replay never calls this. A different key
                # against an already-revoked token remains a conflict.
                raise map_domain_error(exc) from exc
        return self.idempotency.execute(context, status=200, callback=mutation)
