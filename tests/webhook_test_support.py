from __future__ import annotations

import dataclasses
import hashlib
import hmac
import pathlib
import sqlite3
import sys
import unittest
from collections import deque
from collections.abc import Mapping
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from hostpanel_api_webhooks import (  # noqa: E402
    ConflictError,
    DeliveryRequest,
    DeliveryResponse,
    PermanentDeliveryError,
    StateError,
    TransientDeliveryError,
    ValidationError,
    WebhookDeliveryRunner,
    WebhookDestinationStore,
    normalize_webhook_url,
    validate_peer_ip,
    validate_resolved_addresses,
)

NOW = 2_000_000_000
_OPEN_CONNECTIONS = []


@dataclasses.dataclass(frozen=True, slots=True)
class FakeEvent:
    event_id: str = "evt_ABCDEFGHIJKLMNOPQRSTUV"
    destination_id: str = "destination-a"
    event_type: str = "site.updated"
    payload: Mapping[str, Any] = dataclasses.field(default_factory=lambda: {"site_id": "site-a"})
    status: str = "delivering"
    created_at: int = NOW - 10
    available_at: int = NOW - 10
    attempts: int = 1
    max_attempts: int = 3
    lease_owner: str | None = "delivery-a"
    lease_expires_at: int | None = NOW + 60
    delivered_at: int | None = None
    last_error: str | None = None


class FakeOutbox:
    def __init__(self, events=()):
        self.queue = deque(events)
        self.calls = []
        self.fail_operation = None

    def claim_outbox_event(self, *, worker_id, lease_seconds, now=None):
        self.calls.append(("claim", worker_id, lease_seconds, now))
        if self.fail_operation == "claim":
            raise RuntimeError("storage unavailable")
        return self.queue.popleft() if self.queue else None

    def mark_outbox_delivered(self, event_id, *, worker_id, now=None):
        self.calls.append(("delivered", event_id, worker_id, now))
        if self.fail_operation == "delivered":
            raise RuntimeError("lease lost")
        return dataclasses.replace(FakeEvent(event_id=event_id), status="delivered", delivered_at=now, lease_owner=None, lease_expires_at=None)

    def fail_outbox_event(self, event_id, *, worker_id, error, retry_at=None, now=None):
        self.calls.append(("failed", event_id, worker_id, error, retry_at, now))
        if self.fail_operation == "failed":
            raise RuntimeError("lease lost")
        event = FakeEvent(event_id=event_id)
        status = "retry_wait" if retry_at is not None and event.attempts < event.max_attempts else "failed"
        return dataclasses.replace(event, status=status, available_at=retry_at or now, lease_owner=None, lease_expires_at=None, last_error=error)


class Secrets:
    def __init__(self, values=None, error=None):
        self.values = {"vault:webhooks/destination-a": b"s" * 32} if values is None else values
        self.error = error
        self.calls = []

    def resolve(self, reference):
        self.calls.append(reference)
        if self.error:
            raise self.error
        return self.values.get(reference, b"")


class Transport:
    def __init__(self, response=None, error=None):
        self.response = response or DeliveryResponse(204, {}, "93.184.216.34", True, ("93.184.216.34",))
        self.error = error
        self.requests = []

    def send(self, request):
        self.requests.append(request)
        if self.error:
            raise self.error
        return self.response


class Clock:
    def __init__(self, value=NOW):
        self.value = value

    def __call__(self):
        current = self.value
        self.value += 1
        return current


def signer(secret: bytes, *, event_id: str, payload: bytes, timestamp: int) -> str:
    digest = hmac.new(secret, str(timestamp).encode() + b"\0" + event_id.encode() + b"\0" + payload, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def destination_store(connection=None):
    connection = connection or sqlite3.connect(":memory:")
    store = WebhookDestinationStore(connection)
    store.migrate()
    store.create_destination(
        destination_id="destination-a",
        tenant_id="tenant-a",
        url="https://hooks.example.net/hostpanel",
        secret_reference="vault:webhooks/destination-a",
        event_types=("site.updated", "site.deleted"),
        now=NOW - 100,
    )
    return connection, store


def runner(outbox=None, destinations=None, secrets=None, transport=None, **kwargs):
    if destinations is None:
        _connection, destinations = destination_store()
        _OPEN_CONNECTIONS.append(_connection)
    return WebhookDeliveryRunner(
        outbox or FakeOutbox([FakeEvent()]),
        destinations,
        secrets or Secrets(),
        transport or Transport(),
        signer,
        worker_id="delivery-a",
        clock=kwargs.pop("clock", Clock()),
        **kwargs,
    )


def tearDownModule():
    while _OPEN_CONNECTIONS:
        _OPEN_CONNECTIONS.pop().close()
