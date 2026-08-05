from __future__ import annotations

from .delivery_contracts import OutboxEventLike
from .delivery_helpers import delivery_outcome
from .model import DeliveryOutcome, ValidationError


class DeliveryStateMixin:
    def _retry_delay(self, attempts: int) -> int:
        if not isinstance(attempts, int) or isinstance(attempts, bool) or attempts < 1:
            raise ValidationError("outbox attempts are invalid")
        exponent = min(attempts - 1, 30)
        return min(self.retry_base_seconds * (2**exponent), self.retry_max_seconds)

    def _fail(
        self,
        event: OutboxEventLike,
        error: str,
        *,
        retry_at: int | None,
        now: int,
        http_status: int | None = None,
    ) -> DeliveryOutcome:
        try:
            updated = self.outbox.fail_outbox_event(
                event.event_id,
                worker_id=self.worker_id,
                error=error,
                retry_at=retry_at,
                now=now,
            )
        except RuntimeError:
            return delivery_outcome(event, "lease_lost", http_status=http_status)
        status = "retry_wait" if getattr(updated, "status", None) == "retry_wait" else "failed"
        return delivery_outcome(
            event,
            status,
            http_status=http_status,
            retry_at=retry_at if status == "retry_wait" else None,
        )
