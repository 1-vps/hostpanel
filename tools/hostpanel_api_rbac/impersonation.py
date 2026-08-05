from __future__ import annotations

import sqlite3

from .model import (
    MAX_IMPERSONATION_SECONDS,
    AuditActor,
    ConflictError,
    ImpersonationDecision,
    ImpersonationSession,
    StateError,
    ValidationError,
    audit_actor,
    encode_random,
    identifier,
    now as normalize_now,
    text,
)
from .schema import atomic


class ImpersonationMixin:
    def start_impersonation(
        self,
        *,
        actor_principal_id: str,
        target_principal_id: str,
        tenant_id: str,
        reason: str,
        expires_at: int,
        actor: AuditActor,
        now: int | None = None,
    ) -> ImpersonationSession:
        timestamp = normalize_now(now)
        actor_principal = identifier(actor_principal_id, "actor principal ID")
        target_principal = identifier(target_principal_id, "target principal ID")
        tenant = identifier(tenant_id, "tenant ID")
        session_reason = text(
            reason,
            "impersonation reason",
            minimum=8,
            maximum=500,
        )
        expiry = normalize_now(expires_at)
        normalized_actor = audit_actor(actor)
        if normalized_actor.actor_id != actor_principal:
            raise ValidationError(
                "impersonation audit actor must match actor principal"
            )
        if actor_principal == target_principal:
            raise ValidationError("principal cannot impersonate itself")
        if expiry <= timestamp or expiry - timestamp > MAX_IMPERSONATION_SECONDS:
            raise ValidationError("impersonation expiry is invalid")
        with atomic(self.connection):
            # Acquire SQLite's writer lock before any policy reads. This makes
            # concurrent starts deterministic: one session wins and the other
            # observes the committed active-session uniqueness constraint.
            self.connection.execute(
                "UPDATE hp_api_rbac_state SET schema_version = schema_version WHERE singleton = 1"
            )
            expired = self.connection.execute(
                """
                SELECT session_id FROM hp_api_impersonation_sessions
                WHERE actor_principal_id = ? AND ended_at IS NULL AND expires_at <= ?
                ORDER BY session_id
                """,
                (actor_principal, timestamp),
            ).fetchall()
            for (session_id,) in expired:
                self.connection.execute(
                    """
                    UPDATE hp_api_impersonation_sessions
                    SET ended_at = ?, end_reason = 'expired'
                    WHERE session_id = ? AND ended_at IS NULL
                    """,
                    (timestamp, session_id),
                )
                self._audit(
                    timestamp,
                    normalized_actor,
                    "impersonation.expired",
                    "impersonation_session",
                    session_id,
                    {},
                )
            self._principal(actor_principal)
            target = self._principal(target_principal)
            if target.created_at > timestamp or not target.enabled or (
                target.expires_at is not None and target.expires_at <= timestamp
            ):
                raise StateError("target principal is not active")
            self.authorize(
                principal_id=actor_principal,
                capability="support:impersonate",
                tenant_id=tenant,
                resource_type="principal",
                resource_id=target_principal,
                now=timestamp,
            )
            session_id = encode_random("imp_")
            try:
                self.connection.execute(
                    """
                    INSERT INTO hp_api_impersonation_sessions(
                        session_id, actor_principal_id, target_principal_id,
                        tenant_id, reason, created_at, expires_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session_id,
                        actor_principal,
                        target_principal,
                        tenant,
                        session_reason,
                        timestamp,
                        expiry,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ConflictError(
                    "actor already has an active impersonation session"
                ) from exc
            self._audit(
                timestamp,
                normalized_actor,
                "impersonation.started",
                "impersonation_session",
                session_id,
                {
                    "actor_principal_id": actor_principal,
                    "expires_at": expiry,
                    "target_principal_id": target_principal,
                    "tenant_id": tenant,
                },
            )
        return self._session(session_id)

    def simulate_impersonation(
        self,
        session_id: str,
        *,
        capability: str,
        tenant_id: str,
        resource_type: str | None = None,
        resource_id: str | None = None,
        now: int | None = None,
    ) -> ImpersonationDecision:
        timestamp = normalize_now(now)
        tenant = identifier(tenant_id, "tenant ID")
        with atomic(self.connection):
            session = self._session(session_id)
            if (
                session.ended_at is not None
                or session.expires_at <= timestamp
                or session.created_at > timestamp
            ):
                raise StateError("impersonation session is not active")
            if session.tenant_id != tenant:
                raise StateError("impersonation session is bound to another tenant")
            support = self.simulate(
                principal_id=session.actor_principal_id,
                capability="support:impersonate",
                tenant_id=tenant,
                resource_type="principal",
                resource_id=session.target_principal_id,
                now=timestamp,
            )
            actor_decision = self.simulate(
                principal_id=session.actor_principal_id,
                capability=capability,
                tenant_id=tenant,
                resource_type=resource_type,
                resource_id=resource_id,
                now=timestamp,
            )
            target_decision = self.simulate(
                principal_id=session.target_principal_id,
                capability=capability,
                tenant_id=tenant,
                resource_type=resource_type,
                resource_id=resource_id,
                now=timestamp,
            )
            if not support.allowed:
                return ImpersonationDecision(
                    session,
                    False,
                    "support_permission_revoked",
                    actor_decision,
                    target_decision,
                )
            if not actor_decision.allowed:
                return ImpersonationDecision(
                    session,
                    False,
                    "actor_capability_denied",
                    actor_decision,
                    target_decision,
                )
            if not target_decision.allowed:
                return ImpersonationDecision(
                    session,
                    False,
                    "target_capability_denied",
                    actor_decision,
                    target_decision,
                )
            return ImpersonationDecision(
                session,
                True,
                "intersection_allow",
                actor_decision,
                target_decision,
            )

    def authorize_impersonation(
        self,
        session_id: str,
        *,
        capability: str,
        tenant_id: str,
        actor: AuditActor,
        resource_type: str | None = None,
        resource_id: str | None = None,
        now: int | None = None,
    ) -> ImpersonationDecision:
        from .model import AuthorizationError

        timestamp = normalize_now(now)
        normalized_actor = audit_actor(actor)
        with atomic(self.connection):
            decision = self.simulate_impersonation(
                session_id,
                capability=capability,
                tenant_id=tenant_id,
                resource_type=resource_type,
                resource_id=resource_id,
                now=timestamp,
            )
            if normalized_actor.actor_id != decision.session.actor_principal_id:
                raise ValidationError(
                    "impersonation audit actor must match actor principal"
                )
            self._audit(
                timestamp,
                normalized_actor,
                (
                    "impersonation.action_allowed"
                    if decision.allowed
                    else "impersonation.action_denied"
                ),
                "impersonation_session",
                decision.session.session_id,
                {
                    "capability": decision.actor_decision.capability,
                    "decision_reason": decision.reason,
                    "resource_id": decision.actor_decision.resource_id,
                    "resource_type": decision.actor_decision.resource_type,
                    "target_principal_id": decision.session.target_principal_id,
                    "tenant_id": decision.session.tenant_id,
                },
            )
        if not decision.allowed:
            raise AuthorizationError(
                f"impersonated capability denied: {decision.reason}"
            )
        return decision

    def end_impersonation(
        self,
        session_id: str,
        *,
        reason: str,
        actor: AuditActor,
        now: int | None = None,
    ) -> ImpersonationSession:
        timestamp = normalize_now(now)
        session = self._session(session_id)
        end_reason = text(reason, "impersonation end reason", maximum=500)
        if timestamp < session.created_at:
            raise StateError("impersonation transition cannot predate creation")
        with atomic(self.connection):
            result = self.connection.execute(
                """
                UPDATE hp_api_impersonation_sessions
                SET ended_at = ?, end_reason = ?
                WHERE session_id = ? AND ended_at IS NULL
                """,
                (timestamp, end_reason, session.session_id),
            )
            if result.rowcount != 1:
                raise StateError("impersonation session is already ended")
            self._audit(
                timestamp,
                actor,
                "impersonation.ended",
                "impersonation_session",
                session.session_id,
                {"end_reason": end_reason},
            )
        return self._session(session.session_id)
