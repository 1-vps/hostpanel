from __future__ import annotations

from .model import StateError, WebhookDestination, event_type, identifier
from .urlpolicy import normalize_webhook_url


class DestinationReadMixin:
    def get_destination(self, destination_id: str, *, tenant_id: str | None = None) -> WebhookDestination:
        item = identifier(destination_id, "destination ID")
        tenant = identifier(tenant_id, "tenant ID") if tenant_id is not None else None
        query = "SELECT destination_id, tenant_id, url, host, port, path, secret_ref, enabled, created_at, updated_at, disabled_at, version FROM hp_api_webhook_destinations WHERE destination_id=?"
        parameters: tuple[object, ...] = (item,)
        if tenant is not None:
            query += " AND tenant_id=?"
            parameters = (item, tenant)
        row = self.connection.execute(query, parameters).fetchone()
        if row is None:
            raise StateError("webhook destination does not exist")
        events = self._events(item)
        destination = WebhookDestination(
            row[0], row[1], row[2], row[3], row[4], row[5], row[6],
            events, bool(row[7]), row[8], row[9], row[10], row[11],
        )
        normalized, host, port, path = normalize_webhook_url(destination.url)
        if (normalized, host, port, path) != (destination.url, destination.host, destination.port, destination.path):
            raise StateError("persisted webhook destination URL is invalid")
        return destination

    def resolve_for_delivery(self, destination_id: str, event: str) -> WebhookDestination:
        category = event_type(event)
        destination = self.get_destination(destination_id)
        if not destination.enabled:
            raise StateError("webhook destination is disabled")
        if category not in destination.event_types:
            raise StateError("webhook destination is not subscribed")
        return destination

    def _events(self, destination_id: str) -> tuple[str, ...]:
        return tuple(
            row[0]
            for row in self.connection.execute(
                "SELECT event_type FROM hp_api_webhook_destination_events WHERE destination_id=? ORDER BY event_type",
                (destination_id,),
            )
        )
