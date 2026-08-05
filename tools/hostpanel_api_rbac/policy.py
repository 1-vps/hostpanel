from __future__ import annotations

from .model import (
    AuthorizationError,
    MAX_POLICY_MATCHES,
    PolicyDecision,
    capability as normalize_capability,
    identifier,
    now as normalize_now,
)
from .schema import atomic


class PolicyMixin:
    def simulate(
        self,
        *,
        principal_id: str,
        capability: str,
        tenant_id: str,
        resource_type: str | None = None,
        resource_id: str | None = None,
        now: int | None = None,
    ) -> PolicyDecision:
        timestamp = normalize_now(now)
        principal = identifier(principal_id, "principal ID")
        requested = normalize_capability(capability)
        tenant = identifier(tenant_id, "tenant ID")
        if (resource_type is None) != (resource_id is None):
            from .model import ValidationError

            raise ValidationError(
                "resource type and resource ID must be supplied together"
            )
        kind = (
            None if resource_type is None else identifier(resource_type, "resource type")
        )
        item = None if resource_id is None else identifier(resource_id, "resource ID")
        with atomic(self.connection):
            row = self.connection.execute(
                """
                SELECT enabled, created_at, expires_at
                FROM hp_api_principals WHERE principal_id = ?
                """,
                (principal,),
            ).fetchone()
            if row is None:
                return PolicyDecision(
                    principal,
                    requested,
                    tenant,
                    kind,
                    item,
                    False,
                    "principal_missing",
                    (),
                    (),
                )
            enabled, created_at, expires_at = row
            if (
                not enabled
                or created_at > timestamp
                or (expires_at is not None and expires_at <= timestamp)
            ):
                return PolicyDecision(
                    principal,
                    requested,
                    tenant,
                    kind,
                    item,
                    False,
                    "principal_inactive",
                    (),
                    (),
                )
            rows = self.connection.execute(
                """
                SELECT b.binding_id, c.effect, b.allow_all_tenants,
                       b.allow_all_resources
                FROM hp_api_role_bindings AS b
                JOIN hp_api_roles AS r ON r.role_id = b.role_id
                JOIN hp_api_role_capabilities AS c ON c.role_id = r.role_id
                WHERE b.principal_id = ? AND c.capability = ?
                  AND r.enabled = 1 AND r.created_at <= ? AND r.updated_at <= ?
                  AND b.created_at <= ? AND b.revoked_at IS NULL
                  AND (b.expires_at IS NULL OR b.expires_at > ?)
                ORDER BY b.binding_id
                LIMIT ?
                """,
                (
                    principal,
                    requested,
                    timestamp,
                    timestamp,
                    timestamp,
                    timestamp,
                    MAX_POLICY_MATCHES + 1,
                ),
            ).fetchall()
            if len(rows) > MAX_POLICY_MATCHES:
                return PolicyDecision(
                    principal,
                    requested,
                    tenant,
                    kind,
                    item,
                    False,
                    "policy_too_complex",
                    (),
                    (),
                )
            allows: list[str] = []
            denies: list[str] = []
            for binding_id, effect, all_tenants, all_resources in rows:
                if not all_tenants:
                    tenant_match = self.connection.execute(
                        """
                        SELECT 1 FROM hp_api_binding_tenants
                        WHERE binding_id = ? AND tenant_id = ?
                        """,
                        (binding_id, tenant),
                    ).fetchone()
                    if tenant_match is None:
                        continue
                if not all_resources:
                    if kind is None or item is None:
                        continue
                    resource_match = self.connection.execute(
                        """
                        SELECT 1 FROM hp_api_binding_resources
                        WHERE binding_id = ? AND resource_type = ? AND resource_id = ?
                        """,
                        (binding_id, kind, item),
                    ).fetchone()
                    if resource_match is None:
                        continue
                if effect == "deny":
                    denies.append(binding_id)
                else:
                    allows.append(binding_id)
            if denies:
                return PolicyDecision(
                    principal,
                    requested,
                    tenant,
                    kind,
                    item,
                    False,
                    "explicit_deny",
                    tuple(allows),
                    tuple(denies),
                )
            if allows:
                return PolicyDecision(
                    principal,
                    requested,
                    tenant,
                    kind,
                    item,
                    True,
                    "explicit_allow",
                    tuple(allows),
                    (),
                )
            return PolicyDecision(
                principal,
                requested,
                tenant,
                kind,
                item,
                False,
                "default_deny",
                (),
                (),
            )

    def authorize(self, **request) -> PolicyDecision:
        decision = self.simulate(**request)
        if not decision.allowed:
            raise AuthorizationError(
                f"capability denied: {decision.reason}"
            )
        return decision
