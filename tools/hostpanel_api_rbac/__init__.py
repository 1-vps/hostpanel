"""HostPanel capability RBAC, simulation, and impersonation primitives."""

from .model import (
    AuditActor,
    AuditEvent,
    AuthorizationError,
    ConflictError,
    ImpersonationDecision,
    ImpersonationSession,
    PolicyDecision,
    Principal,
    RbacError,
    Role,
    RoleBinding,
    StateError,
    ValidationError,
)
from .store import ApiRbacStore

__all__ = (
    "ApiRbacStore",
    "AuditActor",
    "AuditEvent",
    "AuthorizationError",
    "ConflictError",
    "ImpersonationDecision",
    "ImpersonationSession",
    "PolicyDecision",
    "Principal",
    "RbacError",
    "Role",
    "RoleBinding",
    "StateError",
    "ValidationError",
)
