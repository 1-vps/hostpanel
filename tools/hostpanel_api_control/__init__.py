"""HostPanel API idempotency, durable jobs, outbox, and webhook primitives."""

from .model import (
    AuthenticationError,
    ConflictError,
    ControlPlaneError,
    IdempotencyDecision,
    Job,
    JobCreation,
    JobLog,
    OutboxCreation,
    OutboxEvent,
    ReplayError,
    StateError,
    ValidationError,
)
from .signing import sign_webhook, verify_webhook_signature
from .store import ApiControlStore

__all__ = (
    "ApiControlStore",
    "AuthenticationError",
    "ConflictError",
    "ControlPlaneError",
    "IdempotencyDecision",
    "Job",
    "JobCreation",
    "JobLog",
    "OutboxCreation",
    "OutboxEvent",
    "ReplayError",
    "StateError",
    "ValidationError",
    "sign_webhook",
    "verify_webhook_signature",
)
