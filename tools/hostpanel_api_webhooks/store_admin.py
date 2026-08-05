from __future__ import annotations

from collections.abc import Iterable

from .model import ConflictError, StateError, event_type, identifier, secret_ref, timestamp
from .schema import atomic
from .urlpolicy import normalize_webhook_url


class DestinationAdminMixin:
    def create_destination(
        self,
        *,
        destination_id: str,
        tenant_id: str,
        url: str,
        secret_reference: str,
        event_types: Iterable[str],
        now: int,
    ):
        item = identifier(destination_id, "destination ID")
        tenant = identifier(tenant_id, "tenant ID")
        reference = secret_ref(secret_reference)
        events = self._normalize_events(event_types)
        created = timestamp(now)
        normalized, host, port, path = normalize_webhook_url(url)
        try:
            with atomic(self.connection):
                self.connection.execute(
                    "INSERT INTO hp_api_webhook_destinations(destination_id, tenant_id, url, host, port, path, secret_ref, enabled, created_at, updated_at, disabled_at, version) VALUES(?,?,?,?,?,?,?,1,?,?,NULL,1)",
                    (item, tenant, normalized, host, port, path, reference, created, created),
                )
                self.connection.executemany(
                    "INSERT INTO hp_api_webhook_destination_events(destination_id,event_type) VALUES(?,?)",
                    ((item, value) for value in events),
                )
        except self.sqlite_integrity_error as exc:
            raise ConflictError("webhook destination already exists") from exc
        return self.get_destination(item, tenant_id=tenant)

    def update_destination(
        self,
        destination_id: str,
        *,
        tenant_id: str,
        expected_version: int,
        url: str,
        secret_reference: str,
        event_types: Iterable[str],
        now: int,
    ):
        item = identifier(destination_id, "destination ID")
        tenant = identifier(tenant_id, "tenant ID")
        version = self._version(expected_version)
        reference = secret_ref(secret_reference)
        events = self._normalize_events(event_types)
        changed = timestamp(now)
        normalized, host, port, path = normalize_webhook_url(url)
        with atomic(self.connection):
            result = self.connection.execute(
                "UPDATE hp_api_webhook_destinations SET url=?, host=?, port=?, path=?, secret_ref=?, updated_at=?, version=version+1 WHERE destination_id=? AND tenant_id=? AND version=? AND enabled=1",
                (normalized, host, port, path, reference, changed, item, tenant, version),
            )
            if result.rowcount != 1:
                raise ConflictError("webhook destination version conflict")
            self.connection.execute("DELETE FROM hp_api_webhook_destination_events WHERE destination_id=?", (item,))
            self.connection.executemany(
                "INSERT INTO hp_api_webhook_destination_events(destination_id,event_type) VALUES(?,?)",
                ((item, value) for value in events),
            )
        return self.get_destination(item, tenant_id=tenant)

    def disable_destination(self, destination_id: str, *, tenant_id: str, expected_version: int, now: int):
        item = identifier(destination_id, "destination ID")
        tenant = identifier(tenant_id, "tenant ID")
        version = self._version(expected_version)
        changed = timestamp(now)
        with atomic(self.connection):
            result = self.connection.execute(
                "UPDATE hp_api_webhook_destinations SET enabled=0, disabled_at=?, updated_at=?, version=version+1 WHERE destination_id=? AND tenant_id=? AND version=? AND enabled=1",
                (changed, changed, item, tenant, version),
            )
            if result.rowcount != 1:
                raise ConflictError("webhook destination version conflict")
        return self.get_destination(item, tenant_id=tenant)

    @staticmethod
    def _normalize_events(values: Iterable[str]) -> tuple[str, ...]:
        if isinstance(values, str):
            raise StateError("event filters must be a collection")
        normalized = tuple(sorted({event_type(value) for value in values}))
        if not normalized or len(normalized) > 100:
            raise StateError("event filters are invalid")
        return normalized

    @staticmethod
    def _version(value: int) -> int:
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise StateError("expected version is invalid")
        return value
