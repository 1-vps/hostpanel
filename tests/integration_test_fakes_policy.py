from __future__ import annotations

import dataclasses
import hashlib
import io
import json
import sqlite3

from hostpanel_api_http import RateLimitDecision
from integration_test_models import *


class FakeRbacStore:
    def __init__(self, connection: sqlite3.Connection, trace: list[str]) -> None:
        self.connection = connection
        self.trace = trace
        self.allowed = True
        self.next_role = 1
        self.next_binding = 1
        self.fail_bind = False

    def migrate(self):
        self.connection.execute("CREATE TABLE IF NOT EXISTS hp_api_principals(principal_id TEXT PRIMARY KEY, enabled INTEGER NOT NULL)")

    def authorize(self, *, principal_id, capability, tenant_id, resource_type=None, resource_id=None, now=None):
        self.trace.append("rbac")
        if not self.allowed:
            raise type("AuthorizationError", (RuntimeError,), {})("denied")
        return PolicyDecision(principal_id, capability, tenant_id, resource_type, resource_id, True, "explicit_allow")

    def simulate(self, *, principal_id, capability, tenant_id, resource_type=None, resource_id=None, now=None):
        return PolicyDecision(principal_id, capability, tenant_id, resource_type, resource_id, self.allowed, "explicit_allow" if self.allowed else "default_deny")

    def create_principal(self, *, principal_id, principal_kind, actor, expires_at=None, now=None):
        self.connection.execute("INSERT INTO hp_api_principals VALUES(?, 1)", (principal_id,))
        return RbacPrincipal(principal_id, principal_kind, True, now, expires_at)

    def set_principal_enabled(self, principal_id, enabled, *, actor, now=None):
        result = self.connection.execute("UPDATE hp_api_principals SET enabled = ? WHERE principal_id = ?", (int(enabled), principal_id))
        if result.rowcount != 1:
            raise type("StateError", (RuntimeError,), {})("does not exist")
        return RbacPrincipal(principal_id, "service_account", enabled, 1, None)

    def create_role(self, *, name, allows=(), denies=(), actor, now=None):
        role_id = f"role_test{self.next_role:016d}"
        self.next_role += 1
        return Role(role_id, name, True, tuple(allows), tuple(denies), now, now)

    def bind_role(self, *, principal_id, role_id, tenant_ids=(), allow_all_tenants=False, resources=None, allow_all_resources=False, expires_at=None, actor, now=None):
        if self.fail_bind:
            raise type("ConflictError", (RuntimeError,), {})("forced bind failure")
        binding_id = f"bind_test{self.next_binding:016d}"
        self.next_binding += 1
        return Binding(binding_id, principal_id, role_id, tuple(tenant_ids), allow_all_tenants, (), allow_all_resources, now, expires_at, None)


class FakeWebhookStore:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.destinations: dict[str, Destination] = {}

    def migrate(self):
        self.connection.execute("CREATE TABLE IF NOT EXISTS hp_api_webhook_destinations_stub(id TEXT PRIMARY KEY)")

    def create_destination(self, *, destination_id, tenant_id, url, secret_reference, event_types, now=None):
        item = Destination(destination_id, tenant_id, url, secret_reference, tuple(event_types), True, 1, now, now)
        self.destinations[destination_id] = item
        return item

    def get_destination(self, destination_id, *, tenant_id=None):
        item = self.destinations.get(destination_id)
        if item is None or (tenant_id is not None and item.tenant_id != tenant_id):
            raise type("StateError", (RuntimeError,), {})("destination does not exist")
        return item

    def update_destination(self, destination_id, *, tenant_id, expected_version, url, secret_reference, event_types, now=None):
        item = self.get_destination(destination_id, tenant_id=tenant_id)
        if item.version != expected_version:
            raise type("ConflictError", (RuntimeError,), {})("version")
        updated = dataclasses.replace(item, url=url, secret_reference=secret_reference, event_types=tuple(event_types), version=item.version + 1, updated_at=now)
        self.destinations[destination_id] = updated
        return updated

    def disable_destination(self, destination_id, *, tenant_id, expected_version, now=None):
        item = self.get_destination(destination_id, tenant_id=tenant_id)
        if item.version != expected_version:
            raise type("ConflictError", (RuntimeError,), {})("version")
        updated = dataclasses.replace(item, enabled=False, version=item.version + 1, updated_at=now)
        self.destinations[destination_id] = updated
        return updated


class FakeAuditStore:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.events: list[AuditEvent] = []

    def migrate(self):
        self.connection.execute("CREATE TABLE IF NOT EXISTS hp_api_audit_stub(id TEXT PRIMARY KEY)")

    def append_event(self, *, tenant_id, actor_kind, actor_id, action, outcome, target_kind=None, target_id=None, request_id=None, source_ip=None, reason_code=None, metadata=None, retention_class="standard", occurred_at=None, effective_principal_id=None):
        event = AuditEvent(len(self.events) + 1, f"aud_test{len(self.events)+1:017d}", tenant_id, occurred_at, actor_kind, actor_id, effective_principal_id, action, outcome, target_kind, target_id, request_id, source_ip, reason_code, dict(metadata or {}), retention_class)
        self.events.append(event)
        return event

    def get_event(self, event_id, *, tenant_id):
        for event in self.events:
            if event.event_id == event_id and event.tenant_id == tenant_id:
                return event
        raise type("StateError", (RuntimeError,), {})("event does not exist")

    def search_events(self, *, tenant_id, after=None, limit=100, **filters):
        rows = [event for event in self.events if event.tenant_id == tenant_id]
        for key, value in filters.items():
            if value is not None and hasattr(rows[0], key) if rows else False:
                rows = [event for event in rows if getattr(event, key) == value]
        if after is not None:
            rows = [event for event in rows if (event.occurred_at, event.sequence) > tuple(after)]
        return tuple(rows[:limit])

    def export_jsonl(self, events):
        return b"".join((json.dumps(dataclasses.asdict(event), sort_keys=True, separators=(",", ":")) + "\n").encode() for event in events)

    def export_csv(self, events):
        out = io.StringIO()
        out.write("event_id,action\r\n")
        for event in events:
            out.write(f"{event.event_id},{event.action}\r\n")
        return out.getvalue().encode()

    def export_digest(self, payload):
        return hashlib.sha256(payload).hexdigest()
