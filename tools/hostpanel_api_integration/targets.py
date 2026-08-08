from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from hostpanel_api_http.model import AuthorizationTarget, PrincipalContext, Request

from .errors import ResourceNotFound
from .stores import StoreBundle


class TrustedResolvers:
    def __init__(self, stores: StoreBundle, *, tenant_exists: Callable[[str], bool]) -> None:
        if not callable(tenant_exists):
            raise TypeError("tenant_exists must be callable")
        self.stores = stores
        self.tenant_exists = tenant_exists

    def _tenant(self, path: Mapping[str, str]) -> str:
        tenant = path.get("tenant_id")
        if not isinstance(tenant, str) or not self.tenant_exists(tenant):
            raise ResourceNotFound("tenant does not exist")
        return tenant

    def tenant(self, principal: PrincipalContext, request: Request, path: Mapping[str, str], query) -> AuthorizationTarget:
        return AuthorizationTarget(self._tenant(path))

    def service_account(self, principal: PrincipalContext, request: Request, path: Mapping[str, str], query) -> AuthorizationTarget:
        tenant = self._tenant(path)
        account = path.get("account_id")
        row = self.stores.connection.execute(
            "SELECT allow_all_tenants FROM hp_api_service_accounts WHERE account_id = ?",
            (account,),
        ).fetchone()
        if row is None:
            raise ResourceNotFound("service account does not exist")
        if not row[0]:
            grant = self.stores.connection.execute(
                "SELECT 1 FROM hp_api_account_tenants WHERE account_id = ? AND tenant_id = ?",
                (account, tenant),
            ).fetchone()
            if grant is None:
                raise ResourceNotFound("service account does not belong to tenant")
        return AuthorizationTarget(tenant, "service_account", account)

    def token(self, principal: PrincipalContext, request: Request, path: Mapping[str, str], query) -> AuthorizationTarget:
        target = self.service_account(principal, request, path, query)
        token = path.get("token_id")
        row = self.stores.connection.execute(
            "SELECT 1 FROM hp_api_tokens WHERE token_id = ? AND account_id = ?",
            (token, target.resource_id),
        ).fetchone()
        if row is None:
            raise ResourceNotFound("token does not exist")
        return AuthorizationTarget(target.tenant_id, "api_token", token)

    def job(self, principal: PrincipalContext, request: Request, path: Mapping[str, str], query) -> AuthorizationTarget:
        tenant = self._tenant(path)
        job = path.get("job_id")
        row = self.stores.connection.execute(
            "SELECT 1 FROM hp_api_jobs WHERE job_id = ? AND tenant_id = ?",
            (job, tenant),
        ).fetchone()
        if row is None:
            raise ResourceNotFound("job does not exist")
        return AuthorizationTarget(tenant, "job", job)

    def destination(self, principal: PrincipalContext, request: Request, path: Mapping[str, str], query) -> AuthorizationTarget:
        tenant = self._tenant(path)
        destination = path.get("destination_id")
        try:
            self.stores.webhooks.get_destination(destination, tenant_id=tenant)
        except Exception as exc:
            raise ResourceNotFound("webhook destination does not exist") from exc
        return AuthorizationTarget(tenant, "webhook_destination", destination)

    def audit_event(self, principal: PrincipalContext, request: Request, path: Mapping[str, str], query) -> AuthorizationTarget:
        tenant = self._tenant(path)
        event = path.get("event_id")
        try:
            self.stores.audit.get_event(event, tenant_id=tenant)
        except Exception as exc:
            raise ResourceNotFound("audit event does not exist") from exc
        return AuthorizationTarget(tenant, "audit_event", event)
