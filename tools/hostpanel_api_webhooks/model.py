"""Compatibility exports for webhook destination and delivery models."""

from .destination import WebhookDestination
from .transport import DeliveryOutcome, DeliveryRequest, DeliveryResponse
from .validation import (
    ConflictError,
    PermanentDeliveryError,
    StateError,
    TransientDeliveryError,
    ValidationError,
    WebhookError,
    event_type,
    identifier,
    json_mapping,
    positive_int,
    secret_ref,
    timestamp,
)

__all__ = (
    "ConflictError",
    "DeliveryOutcome",
    "DeliveryRequest",
    "DeliveryResponse",
    "PermanentDeliveryError",
    "StateError",
    "TransientDeliveryError",
    "ValidationError",
    "WebhookDestination",
    "WebhookError",
    "event_type",
    "identifier",
    "json_mapping",
    "positive_int",
    "secret_ref",
    "timestamp",
)
