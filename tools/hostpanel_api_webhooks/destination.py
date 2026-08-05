from __future__ import annotations

import dataclasses

from .validation import ValidationError, event_type, identifier, secret_ref, timestamp


@dataclasses.dataclass(frozen=True, slots=True)
class WebhookDestination:
    destination_id: str
    tenant_id: str
    url: str
    host: str
    port: int
    path: str
    secret_ref: str
    event_types: tuple[str, ...]
    enabled: bool
    created_at: int
    updated_at: int
    disabled_at: int | None
    version: int

    def __post_init__(self) -> None:
        identifier(self.destination_id, "destination ID")
        identifier(self.tenant_id, "tenant ID")
        secret_ref(self.secret_ref)
        if not isinstance(self.url, str) or not isinstance(self.host, str) or not isinstance(self.path, str):
            raise ValidationError("persisted webhook destination is invalid")
        if self.port != 443 or not self.path.startswith("/"):
            raise ValidationError("persisted webhook destination is invalid")
        if not isinstance(self.event_types, tuple) or not self.event_types or self.event_types != tuple(sorted(set(self.event_types))):
            raise ValidationError("persisted webhook event filter is invalid")
        for item in self.event_types:
            event_type(item)
        if not isinstance(self.enabled, bool):
            raise ValidationError("persisted webhook destination state is invalid")
        timestamp(self.created_at)
        timestamp(self.updated_at)
        if self.updated_at < self.created_at:
            raise ValidationError("persisted webhook timestamps are invalid")
        if self.disabled_at is not None:
            timestamp(self.disabled_at)
        if self.enabled == (self.disabled_at is not None):
            raise ValidationError("persisted webhook disable state is invalid")
        if not isinstance(self.version, int) or isinstance(self.version, bool) or self.version < 1:
            raise ValidationError("persisted webhook version is invalid")
