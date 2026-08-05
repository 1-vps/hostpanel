from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from typing import Any

from .model import (
    MAX_AUDIT_PAGE,
    AuditActor,
    AuditEvent,
    ImpersonationSession,
    Principal,
    Role,
    RoleBinding,
    SESSION_ID_RE,
    StateError,
    ValidationError,
    audit_actor,
    decode_metadata,
    encode_random,
    identifier,
    integer,
    metadata_json,
)
from .schema import initialize_connection, migrate as migrate_schema


class StoreBase:
    def __init__(self, connection: sqlite3.Connection):
        if not isinstance(connection, sqlite3.Connection):
            raise TypeError("connection must be sqlite3.Connection")
        self.connection = connection
        initialize_connection(connection)

    def migrate(self) -> None:
        migrate_schema(self.connection)

    def list_audit_events(
        self,
        *,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> tuple[AuditEvent, ...]:
        cursor = integer(
            after_sequence,
            "audit cursor",
            minimum=0,
            maximum=2**63 - 1,
        )
        page = integer(limit, "audit page size", minimum=1, maximum=MAX_AUDIT_PAGE)
        rows = self.connection.execute(
            """
            SELECT sequence, event_id, occurred_at, actor_kind, actor_id,
                   action, target_kind, target_id, metadata_json
            FROM hp_api_rbac_audit_events
            WHERE sequence > ?
            ORDER BY sequence
            LIMIT ?
            """,
            (cursor, page),
        ).fetchall()
        return tuple(
            AuditEvent(
                sequence,
                event_id,
                occurred_at,
                actor_kind,
                actor_id,
                action,
                target_kind,
                target_id,
                decode_metadata(metadata),
            )
            for (
                sequence,
                event_id,
                occurred_at,
                actor_kind,
                actor_id,
                action,
                target_kind,
                target_id,
                metadata,
            ) in rows
        )

    def _audit(
        self,
        timestamp: int,
        actor: AuditActor,
        action: str,
        target_kind: str,
        target_id: str,
        metadata: Mapping[str, Any],
    ) -> None:
        normalized_actor = audit_actor(actor)
        self.connection.execute(
            """
            INSERT INTO hp_api_rbac_audit_events(
                event_id, occurred_at, actor_kind, actor_id, action,
                target_kind, target_id, metadata_json
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                encode_random("rbac_evt_"),
                timestamp,
                normalized_actor.kind,
                normalized_actor.actor_id,
                identifier(action, "audit action"),
                identifier(target_kind, "audit target kind"),
                identifier(target_id, "audit target ID"),
                metadata_json(metadata),
            ),
        )

    def _principal(self, principal_id: str) -> Principal:
        principal = identifier(principal_id, "principal ID")
        row = self.connection.execute(
            """
            SELECT principal_kind, enabled, created_at, expires_at
            FROM hp_api_principals WHERE principal_id = ?
            """,
            (principal,),
        ).fetchone()
        if row is None:
            raise StateError("principal does not exist")
        return Principal(principal, row[0], bool(row[1]), row[2], row[3])

    def _role(self, role_id: str) -> Role:
        role = identifier(role_id, "role ID")
        row = self.connection.execute(
            "SELECT name, enabled, created_at, updated_at FROM hp_api_roles WHERE role_id = ?",
            (role,),
        ).fetchone()
        if row is None:
            raise StateError("role does not exist")
        capabilities = self.connection.execute(
            """
            SELECT capability, effect FROM hp_api_role_capabilities
            WHERE role_id = ? ORDER BY capability
            """,
            (role,),
        ).fetchall()
        allows = tuple(value for value, effect in capabilities if effect == "allow")
        denies = tuple(value for value, effect in capabilities if effect == "deny")
        return Role(role, row[0], bool(row[1]), allows, denies, row[2], row[3])

    def _binding(self, binding_id: str) -> RoleBinding:
        binding = identifier(binding_id, "binding ID")
        row = self.connection.execute(
            """
            SELECT principal_id, role_id, allow_all_tenants,
                   allow_all_resources, created_at, expires_at, revoked_at
            FROM hp_api_role_bindings WHERE binding_id = ?
            """,
            (binding,),
        ).fetchone()
        if row is None:
            raise StateError("role binding does not exist")
        tenants = tuple(
            value[0]
            for value in self.connection.execute(
                "SELECT tenant_id FROM hp_api_binding_tenants WHERE binding_id = ? ORDER BY tenant_id",
                (binding,),
            )
        )
        resources = tuple(
            (value[0], value[1])
            for value in self.connection.execute(
                """
                SELECT resource_type, resource_id
                FROM hp_api_binding_resources
                WHERE binding_id = ?
                ORDER BY resource_type, resource_id
                """,
                (binding,),
            )
        )
        return RoleBinding(
            binding,
            row[0],
            row[1],
            tenants,
            bool(row[2]),
            resources,
            bool(row[3]),
            row[4],
            row[5],
            row[6],
        )

    def _session(self, session_id: str) -> ImpersonationSession:
        session = identifier(session_id, "impersonation session ID")
        if SESSION_ID_RE.fullmatch(session) is None:
            raise ValidationError("impersonation session ID is invalid")
        row = self.connection.execute(
            """
            SELECT actor_principal_id, target_principal_id, tenant_id, reason,
                   created_at, expires_at, ended_at, end_reason
            FROM hp_api_impersonation_sessions WHERE session_id = ?
            """,
            (session,),
        ).fetchone()
        if row is None:
            raise StateError("impersonation session does not exist")
        return ImpersonationSession(session, *row)
