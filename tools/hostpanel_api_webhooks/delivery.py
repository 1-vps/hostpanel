from __future__ import annotations

from collections.abc import Callable

from .delivery_contracts import OutboxStore, SecretProvider, WebhookTransport
from .delivery_helpers import (
    delivery_outcome,
    public_destination_error,
    retry_after,
    retryable_status,
    webhook_body,
)
from .delivery_state import DeliveryStateMixin
from .model import (
    DeliveryOutcome,
    DeliveryRequest,
    PermanentDeliveryError,
    StateError,
    TransientDeliveryError,
    ValidationError,
    positive_int,
)
from .store import WebhookDestinationStore
from .urlpolicy import validate_peer_ip, validate_resolved_addresses


class WebhookDeliveryRunner(DeliveryStateMixin):
    def __init__(
        self,
        outbox: OutboxStore,
        destinations: WebhookDestinationStore,
        secrets: SecretProvider,
        transport: WebhookTransport,
        signer: Callable[..., str],
        *,
        worker_id: str,
        clock: Callable[[], int],
        lease_seconds: int = 60,
        connect_timeout_seconds: int = 5,
        read_timeout_seconds: int = 10,
        retry_base_seconds: int = 5,
        retry_max_seconds: int = 3600,
    ) -> None:
        if not callable(signer):
            raise ValidationError("webhook signer is invalid")
        if not isinstance(worker_id, str) or not worker_id.strip() or len(worker_id) > 128:
            raise ValidationError("delivery worker ID is invalid")
        if not callable(clock):
            raise ValidationError("delivery clock is invalid")
        self.outbox = outbox
        self.destinations = destinations
        self.secrets = secrets
        self.transport = transport
        self.signer = signer
        self.worker_id = worker_id.strip()
        self.clock = clock
        self.lease_seconds = positive_int(lease_seconds, "lease duration", 3600)
        self.connect_timeout_seconds = positive_int(connect_timeout_seconds, "connect timeout", 60)
        self.read_timeout_seconds = positive_int(read_timeout_seconds, "read timeout", 300)
        self.retry_base_seconds = positive_int(retry_base_seconds, "retry base", 3600)
        self.retry_max_seconds = positive_int(retry_max_seconds, "retry maximum", 86_400)
        if self.retry_base_seconds > self.retry_max_seconds:
            raise ValidationError("retry base exceeds retry maximum")

    def run_once(self) -> DeliveryOutcome:
        now = self.clock()
        try:
            event = self.outbox.claim_outbox_event(
                worker_id=self.worker_id,
                lease_seconds=self.lease_seconds,
                now=now,
            )
        except RuntimeError:
            return DeliveryOutcome("lease_lost")
        if event is None:
            return DeliveryOutcome("idle")
        try:
            destination = self.destinations.resolve_for_delivery(event.destination_id, event.event_type)
        except StateError as exc:
            return self._fail(event, public_destination_error(exc), retry_at=None, now=self.clock())
        try:
            secret = self.secrets.resolve(destination.secret_ref)
            if not isinstance(secret, bytes) or not 32 <= len(secret) <= 128:
                raise PermanentDeliveryError("webhook secret is unavailable")
            body = webhook_body(event)
            signed_at = self.clock()
            signature = self.signer(secret, event_id=event.event_id, payload=body, timestamp=signed_at)
            if not isinstance(signature, str) or len(signature) > 256 or "\r" in signature or "\n" in signature:
                raise PermanentDeliveryError("webhook signature is invalid")
            request = DeliveryRequest(
                destination.url,
                destination.host,
                destination.port,
                destination.path,
                (
                    ("Content-Type", "application/json"),
                    ("User-Agent", "HostPanel-Webhook/1"),
                    ("X-HostPanel-Event-Id", event.event_id),
                    ("X-HostPanel-Event-Type", event.event_type),
                    ("X-HostPanel-Signature", signature),
                ),
                body,
                self.connect_timeout_seconds,
                self.read_timeout_seconds,
                False,
            )
            response = self.transport.send(request)
            resolved = validate_resolved_addresses(response.resolved_ips)
            peer = validate_peer_ip(response.peer_ip)
            if peer not in resolved or not response.tls_verified or response.redirected:
                raise PermanentDeliveryError("webhook transport policy failed")
        except TransientDeliveryError:
            transition_now = self.clock()
            return self._fail(
                event,
                "webhook delivery failed",
                retry_at=transition_now + self._retry_delay(event.attempts),
                now=transition_now,
            )
        except (PermanentDeliveryError, ValidationError, ValueError):
            return self._fail(event, "webhook delivery failed", retry_at=None, now=self.clock())
        except Exception:
            transition_now = self.clock()
            return self._fail(
                event,
                "webhook delivery failed",
                retry_at=transition_now + self._retry_delay(event.attempts),
                now=transition_now,
            )

        if 200 <= response.status <= 299:
            try:
                self.outbox.mark_outbox_delivered(event.event_id, worker_id=self.worker_id, now=self.clock())
            except RuntimeError:
                return delivery_outcome(event, "lease_lost", http_status=response.status)
            return delivery_outcome(event, "delivered", http_status=response.status)

        error = f"webhook returned HTTP {response.status}"
        if retryable_status(response.status):
            delay = retry_after(response.headers)
            if delay is None:
                delay = self._retry_delay(event.attempts)
            transition_now = self.clock()
            return self._fail(
                event,
                error,
                retry_at=transition_now + delay,
                now=transition_now,
                http_status=response.status,
            )
        return self._fail(event, error, retry_at=None, now=self.clock(), http_status=response.status)
