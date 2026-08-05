from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from .model import DeliveryRequest, DeliveryResponse


class OutboxEventLike(Protocol):
    event_id: str
    destination_id: str
    event_type: str
    payload: Mapping[str, Any]
    created_at: int
    attempts: int


class OutboxStore(Protocol):
    def claim_outbox_event(self, *, worker_id: str, lease_seconds: int, now: int | None = None) -> OutboxEventLike | None: ...
    def mark_outbox_delivered(self, event_id: str, *, worker_id: str, now: int | None = None) -> OutboxEventLike: ...
    def fail_outbox_event(self, event_id: str, *, worker_id: str, error: str, retry_at: int | None = None, now: int | None = None) -> OutboxEventLike: ...


class SecretProvider(Protocol):
    def resolve(self, secret_reference: str) -> bytes: ...


class WebhookTransport(Protocol):
    def send(self, request: DeliveryRequest) -> DeliveryResponse: ...
