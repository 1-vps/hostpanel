from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from hostpanel_api_http.model import (
    AccessGrant,
    ApiProblem,
    AuthorizationTarget,
    PrincipalContext,
    Request,
    TargetResolver,
    scope,
)

from .identity import TokenIdentityAuthenticator
from .stores import StoreBundle


class StackAccessController:
    """Combine token scope enforcement and RBAC capability enforcement."""

    def __init__(
        self,
        stores: StoreBundle,
        *,
        scope_to_capability: Callable[[str], str] | None = None,
    ) -> None:
        self.stores = stores
        self.identity = TokenIdentityAuthenticator(stores.tokens)
        self.scope_to_capability = scope_to_capability or (lambda value: value)

    def authorize(
        self,
        *,
        raw_token: str,
        required_scope: str,
        request: Request,
        path_parameters: Mapping[str, str],
        query_parameters: Mapping[str, tuple[str, ...]],
        resolve_target: TargetResolver,
        now: int,
    ) -> AccessGrant:
        required = scope(required_scope)
        with self.stores.atomic():
            try:
                identity = self.identity.authenticate(raw_token, request.source_ip, now)
            except Exception as exc:
                if exc.__class__.__name__ in {"AuthenticationError"}:
                    raise ApiProblem(
                        401,
                        "invalid_token",
                        "The bearer token is invalid.",
                        headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
                    ) from exc
                raise
            principal = PrincipalContext(
                identity.account_id,
                "service_account",
                identity.token_id,
            )
            try:
                target = resolve_target(
                    principal,
                    request,
                    path_parameters,
                    query_parameters,
                )
            except ApiProblem:
                raise
            except Exception as exc:
                raise ApiProblem(404, "not_found", "The requested resource was not found.") from exc
            if not isinstance(target, AuthorizationTarget):
                raise TypeError("target resolver must return AuthorizationTarget")
            try:
                verified = self.stores.tokens.authenticate(
                    raw_token,
                    required_scope=required,
                    tenant_id=target.tenant_id,
                    source_ip=request.source_ip,
                    resource_type=target.resource_type,
                    resource_id=target.resource_id,
                    now=now,
                    touch=True,
                )
                if (
                    verified.account_id != identity.account_id
                    or verified.token_id != identity.token_id
                ):
                    raise RuntimeError("token identity changed within one snapshot")
                self.stores.rbac.authorize(
                    principal_id=identity.account_id,
                    capability=self.scope_to_capability(required),
                    tenant_id=target.tenant_id,
                    resource_type=target.resource_type,
                    resource_id=target.resource_id,
                    now=now,
                )
            except ApiProblem:
                raise
            except Exception as exc:
                name = exc.__class__.__name__
                if name == "AuthenticationError":
                    raise ApiProblem(
                        401,
                        "invalid_token",
                        "The bearer token is invalid.",
                        headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
                    ) from exc
                if name in {"AuthorizationError"}:
                    raise ApiProblem(403, "forbidden", "The requested operation is not permitted.") from exc
                raise
            return AccessGrant(principal, target)
