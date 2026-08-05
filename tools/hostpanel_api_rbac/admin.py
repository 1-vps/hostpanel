from __future__ import annotations

from collections.abc import Iterable, Mapping
import sqlite3

from .model import (
    BINDING_ID_RE,
    MAX_RESOURCES,
    MAX_TENANTS,
    PRINCIPAL_KINDS,
    ROLE_ID_RE,
    AuditActor,
    ConflictError,
    Principal,
    Role,
    RoleBinding,
    StateError,
    ValidationError,
    capabilities,
    encode_random,
    identifier,
    identifiers,
    now as normalize_now,
    resources as normalize_resources,
    text,
)
from .schema import atomic


class AdminMixin:
    def create_principal(
        self,
        *,
        principal_id: str,
        principal_kind: str,
        actor: AuditActor,
        expires_at: int | None = None,
        now: int | None = None,
    ) -> Principal:
        timestamp = normalize_now(now)
        principal = identifier(principal_id, "principal ID")
        if principal_kind not in PRINCIPAL_KINDS:
            raise ValidationError("principal kind is invalid")
        expiry = None if expires_at is None else normalize_now(expires_at)
        if expiry is not None and expiry <= timestamp:
            raise ValidationError("principal expiry must be in the future")
        with atomic(self.connection):
            try:
                self.connection.execute(
                    """
                    INSERT INTO hp_api_principals(
                        principal_id, principal_kind, enabled, created_at, expires_at
                    ) VALUES(?, ?, 1, ?, ?)
                    """,
                    (principal, principal_kind, timestamp, expiry),
                )
            except sqlite3.IntegrityError as exc:
                if self.connection.execute(
                    "SELECT 1 FROM hp_api_principals WHERE principal_id = ?",
                    (principal,),
                ).fetchone() is not None:
                    raise ConflictError("principal already exists") from exc
                raise
            self._audit(
                timestamp,
                actor,
                "principal.created",
                "principal",
                principal,
                {"expires_at": expiry, "principal_kind": principal_kind},
            )
        return self._principal(principal)

    def set_principal_enabled(
        self,
        principal_id: str,
        enabled: bool,
        *,
        actor: AuditActor,
        now: int | None = None,
    ) -> Principal:
        principal = identifier(principal_id, "principal ID")
        if not isinstance(enabled, bool):
            raise ValidationError("principal enabled state must be boolean")
        timestamp = normalize_now(now)
        with atomic(self.connection):
            row = self.connection.execute(
                "SELECT created_at FROM hp_api_principals WHERE principal_id = ?",
                (principal,),
            ).fetchone()
            if row is None:
                raise StateError("principal does not exist")
            if timestamp < row[0]:
                raise StateError("principal transition cannot predate creation")
            self.connection.execute(
                "UPDATE hp_api_principals SET enabled = ? WHERE principal_id = ?",
                (int(enabled), principal),
            )
            self._audit(
                timestamp,
                actor,
                "principal.enabled" if enabled else "principal.disabled",
                "principal",
                principal,
                {},
            )
        return self._principal(principal)

    def create_role(
        self,
        *,
        name: str,
        allows: Iterable[str] = (),
        denies: Iterable[str] = (),
        actor: AuditActor,
        now: int | None = None,
    ) -> Role:
        timestamp = normalize_now(now)
        role_name = text(name, "role name")
        allowed = capabilities(allows)
        denied = capabilities(denies)
        if not allowed and not denied:
            raise ValidationError("role must define at least one capability")
        overlap = set(allowed).intersection(denied)
        if overlap:
            raise ValidationError("role cannot both allow and deny a capability")
        role_id = encode_random("role_")
        with atomic(self.connection):
            self.connection.execute(
                """
                INSERT INTO hp_api_roles(role_id, name, enabled, created_at, updated_at)
                VALUES(?, ?, 1, ?, ?)
                """,
                (role_id, role_name, timestamp, timestamp),
            )
            self._write_capabilities(role_id, allowed, denied)
            self._audit(
                timestamp,
                actor,
                "role.created",
                "role",
                role_id,
                {"allow_count": len(allowed), "deny_count": len(denied)},
            )
        return self._role(role_id)

    def replace_role_capabilities(
        self,
        role_id: str,
        *,
        allows: Iterable[str] = (),
        denies: Iterable[str] = (),
        actor: AuditActor,
        now: int | None = None,
    ) -> Role:
        role = identifier(role_id, "role ID")
        if ROLE_ID_RE.fullmatch(role) is None:
            raise ValidationError("role ID is invalid")
        allowed = capabilities(allows)
        denied = capabilities(denies)
        if not allowed and not denied:
            raise ValidationError("role must define at least one capability")
        if set(allowed).intersection(denied):
            raise ValidationError("role cannot both allow and deny a capability")
        timestamp = normalize_now(now)
        with atomic(self.connection):
            row = self.connection.execute(
                "SELECT created_at FROM hp_api_roles WHERE role_id = ?",
                (role,),
            ).fetchone()
            if row is None:
                raise StateError("role does not exist")
            if timestamp < row[0]:
                raise StateError("role transition cannot predate creation")
            self.connection.execute(
                "DELETE FROM hp_api_role_capabilities WHERE role_id = ?",
                (role,),
            )
            self._write_capabilities(role, allowed, denied)
            self.connection.execute(
                "UPDATE hp_api_roles SET updated_at = ? WHERE role_id = ?",
                (timestamp, role),
            )
            self._audit(
                timestamp,
                actor,
                "role.capabilities_replaced",
                "role",
                role,
                {"allow_count": len(allowed), "deny_count": len(denied)},
            )
        return self._role(role)

    def set_role_enabled(
        self,
        role_id: str,
        enabled: bool,
        *,
        actor: AuditActor,
        now: int | None = None,
    ) -> Role:
        role = identifier(role_id, "role ID")
        if ROLE_ID_RE.fullmatch(role) is None or not isinstance(enabled, bool):
            raise ValidationError("role state is invalid")
        timestamp = normalize_now(now)
        with atomic(self.connection):
            row = self.connection.execute(
                "SELECT created_at FROM hp_api_roles WHERE role_id = ?",
                (role,),
            ).fetchone()
            if row is None:
                raise StateError("role does not exist")
            if timestamp < row[0]:
                raise StateError("role transition cannot predate creation")
            self.connection.execute(
                "UPDATE hp_api_roles SET enabled = ?, updated_at = ? WHERE role_id = ?",
                (int(enabled), timestamp, role),
            )
            self._audit(
                timestamp,
                actor,
                "role.enabled" if enabled else "role.disabled",
                "role",
                role,
                {},
            )
        return self._role(role)

    def bind_role(
        self,
        *,
        principal_id: str,
        role_id: str,
        tenant_ids: Iterable[str] = (),
        allow_all_tenants: bool = False,
        resources: Mapping[str, Iterable[str]] | None = None,
        allow_all_resources: bool = False,
        expires_at: int | None = None,
        actor: AuditActor,
        now: int | None = None,
    ) -> RoleBinding:
        timestamp = normalize_now(now)
        principal = identifier(principal_id, "principal ID")
        role = identifier(role_id, "role ID")
        if ROLE_ID_RE.fullmatch(role) is None:
            raise ValidationError("role ID is invalid")
        if not isinstance(allow_all_tenants, bool) or not isinstance(
            allow_all_resources, bool
        ):
            raise ValidationError("binding scope flags must be boolean")
        tenants = identifiers(tenant_ids, "tenant ID", maximum=MAX_TENANTS)
        resource_grants = normalize_resources(resources)
        if allow_all_tenants and tenants:
            raise ValidationError("all-tenant binding cannot also list tenants")
        if not allow_all_tenants and not tenants:
            raise ValidationError("binding must grant tenants or all tenants")
        if allow_all_resources and resource_grants:
            raise ValidationError("all-resource binding cannot also list resources")
        if not allow_all_resources and not resource_grants:
            raise ValidationError("binding must grant resources or all resources")
        expiry = None if expires_at is None else normalize_now(expires_at)
        if expiry is not None and expiry <= timestamp:
            raise ValidationError("binding expiry must be in the future")
        binding = encode_random("bind_")
        with atomic(self.connection):
            principal_row = self.connection.execute(
                "SELECT created_at, expires_at FROM hp_api_principals WHERE principal_id = ?",
                (principal,),
            ).fetchone()
            if principal_row is None:
                raise StateError("principal does not exist")
            role_row = self.connection.execute(
                "SELECT created_at FROM hp_api_roles WHERE role_id = ?",
                (role,),
            ).fetchone()
            if role_row is None:
                raise StateError("role does not exist")
            if timestamp < max(principal_row[0], role_row[0]):
                raise StateError("binding cannot predate principal or role")
            if principal_row[1] is not None and (
                expiry is None or expiry > principal_row[1]
            ):
                raise ValidationError("binding expiry exceeds principal expiry")
            self.connection.execute(
                """
                INSERT INTO hp_api_role_bindings(
                    binding_id, principal_id, role_id, allow_all_tenants,
                    allow_all_resources, created_at, expires_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    binding,
                    principal,
                    role,
                    int(allow_all_tenants),
                    int(allow_all_resources),
                    timestamp,
                    expiry,
                ),
            )
            self.connection.executemany(
                "INSERT INTO hp_api_binding_tenants(binding_id, tenant_id) VALUES(?, ?)",
                ((binding, tenant) for tenant in tenants),
            )
            self.connection.executemany(
                """
                INSERT INTO hp_api_binding_resources(
                    binding_id, resource_type, resource_id
                ) VALUES(?, ?, ?)
                """,
                (
                    (binding, resource_type, resource_id)
                    for resource_type, resource_id in resource_grants
                ),
            )
            self._audit(
                timestamp,
                actor,
                "binding.created",
                "role_binding",
                binding,
                {
                    "allow_all_resources": allow_all_resources,
                    "allow_all_tenants": allow_all_tenants,
                    "expires_at": expiry,
                    "principal_id": principal,
                    "resource_count": len(resource_grants),
                    "role_id": role,
                    "tenant_count": len(tenants),
                },
            )
        return self._binding(binding)

    def revoke_binding(
        self,
        binding_id: str,
        *,
        actor: AuditActor,
        now: int | None = None,
    ) -> RoleBinding:
        binding = identifier(binding_id, "binding ID")
        if BINDING_ID_RE.fullmatch(binding) is None:
            raise ValidationError("binding ID is invalid")
        timestamp = normalize_now(now)
        with atomic(self.connection):
            result = self.connection.execute(
                """
                UPDATE hp_api_role_bindings SET revoked_at = ?
                WHERE binding_id = ? AND revoked_at IS NULL AND created_at <= ?
                """,
                (timestamp, binding, timestamp),
            )
            if result.rowcount != 1:
                raise StateError("binding is missing, revoked, or from the future")
            self._audit(
                timestamp,
                actor,
                "binding.revoked",
                "role_binding",
                binding,
                {},
            )
        return self._binding(binding)

    def _write_capabilities(
        self,
        role_id: str,
        allows: tuple[str, ...],
        denies: tuple[str, ...],
    ) -> None:
        self.connection.executemany(
            """
            INSERT INTO hp_api_role_capabilities(role_id, capability, effect)
            VALUES(?, ?, 'allow')
            """,
            ((role_id, value) for value in allows),
        )
        self.connection.executemany(
            """
            INSERT INTO hp_api_role_capabilities(role_id, capability, effect)
            VALUES(?, ?, 'deny')
            """,
            ((role_id, value) for value in denies),
        )
