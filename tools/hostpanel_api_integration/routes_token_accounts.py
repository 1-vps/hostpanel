from __future__ import annotations

from hostpanel_api_http.model import ApiProblem, RequestContext

from .domain import map_domain_error
from .route_helpers import next_cursor, page_position, require_cursor_int, require_cursor_text
from .serialization import envelope, json_value
from .validation import body_object, integer_field, string_list_field, text_field


class TokenAccountRouteHandlers:
    def _token_actor(self, context: RequestContext):
        from hostpanel_api_tokens import Actor
        return Actor("service_account", context.principal.principal_id)

    def _rbac_actor(self, context: RequestContext):
        from hostpanel_api_rbac import AuditActor
        return AuditActor("service_account", context.principal.principal_id)

    def create_service_account(self, context: RequestContext):
        data = body_object(context, allowed={"name", "scopes", "source_cidrs", "expires_at"}, required={"name", "scopes"})
        name = text_field(data, "name")
        scopes = string_list_field(data, "scopes")
        cidrs = string_list_field(data, "source_cidrs", required=False) or ()
        expires = integer_field(data, "expires_at", required=False)
        tenant = context.target.tenant_id

        def mutation():
            try:
                account = self.stores.tokens.create_service_account(
                    name=name, scopes=scopes, tenant_ids=(tenant,), allow_all_tenants=False,
                    source_cidrs=cidrs, expires_at=expires, actor=self._token_actor(context), now=context.now,
                )
                principal = self.stores.rbac.create_principal(
                    principal_id=account.account_id, principal_kind="service_account",
                    expires_at=expires, actor=self._rbac_actor(context), now=context.now,
                )
                role = self.stores.rbac.create_role(
                    name=f"API account {account.account_id}", allows=scopes,
                    actor=self._rbac_actor(context), now=context.now,
                )
                binding = self.stores.rbac.bind_role(
                    principal_id=account.account_id, role_id=role.role_id,
                    tenant_ids=(tenant,), allow_all_tenants=False,
                    allow_all_resources=True, actor=self._rbac_actor(context),
                    expires_at=expires, now=context.now,
                )
                event = self.audit_writer.append(
                    context, action="service_account.created", target_kind="service_account",
                    target_id=account.account_id,
                    metadata={"role_id": role.role_id, "binding_id": binding.binding_id, "scope_count": len(scopes)},
                    retention_class="security",
                )
                return {"service_account": json_value(account), "principal": json_value(principal), "role": json_value(role), "binding": json_value(binding), "audit_event_id": event.event_id}
            except Exception as exc:
                raise map_domain_error(exc) from exc
        return self.idempotency.execute(context, status=201, callback=mutation)

    def list_service_accounts(self, context: RequestContext):
        position, limit = page_position(
            context,
            self.cursor_codec,
            operation="listServiceAccounts",
        )
        after = None
        if position is not None:
            after = (
                require_cursor_int(position, "created_at"),
                require_cursor_text(position, "account_id"),
            )
        try:
            rows = self.stores.tokens.list_service_accounts(
                tenant_id=context.target.tenant_id,
                after=after,
                limit=limit + 1,
            )
        except Exception as exc:
            raise map_domain_error(exc) from exc
        has_more = len(rows) > limit
        items = rows[:limit]
        cursor = None
        if has_more and items:
            last = items[-1]
            cursor = next_cursor(
                context,
                self.cursor_codec,
                operation="listServiceAccounts",
                position={
                    "created_at": last.created_at,
                    "account_id": last.account_id,
                },
            )
        return {"service_accounts": json_value(items), "next_cursor": cursor}

    def get_service_account(self, context: RequestContext):
        account_id = context.path_parameters["account_id"]
        try:
            return envelope(
                "service_account",
                self.stores.tokens.get_service_account(account_id),
            )
        except Exception as exc:
            raise map_domain_error(exc, missing_is_not_found=True) from exc

    def set_service_account_state(self, context: RequestContext):
        data = body_object(context, allowed={"enabled"}, required={"enabled"})
        enabled = data["enabled"]
        if not isinstance(enabled, bool):
            raise ApiProblem(400, "invalid_field", "Field 'enabled' must be boolean.")
        account_id = context.path_parameters["account_id"]

        def mutation():
            try:
                self.stores.tokens.set_service_account_enabled(account_id, enabled, actor=self._token_actor(context), now=context.now)
                principal = self.stores.rbac.set_principal_enabled(account_id, enabled, actor=self._rbac_actor(context), now=context.now)
                account = self.stores.tokens.get_service_account(account_id)
                event = self.audit_writer.append(
                    context, action="service_account.enabled" if enabled else "service_account.disabled",
                    target_kind="service_account", target_id=account_id,
                    metadata={"enabled": enabled}, retention_class="security",
                )
                return {"service_account": json_value(account), "principal": json_value(principal), "audit_event_id": event.event_id}
            except Exception as exc:
                raise map_domain_error(exc) from exc
        return self.idempotency.execute(context, status=200, callback=mutation)
