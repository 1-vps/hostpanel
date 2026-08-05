from __future__ import annotations

import contextlib
import secrets
import sqlite3
from dataclasses import dataclass
from typing import Any, Iterator

from .errors import ConfigurationError


_REQUIRED_METHODS = {
    "tokens": (
        "authenticate_identity",
        "authenticate",
        "create_service_account",
        "get_service_account",
        "list_service_accounts",
        "issue_token",
        "list_tokens",
        "revoke_token",
        "migrate",
    ),
    "control": (
        "begin_idempotent_request",
        "complete_idempotent_request",
        "create_job",
        "get_job",
        "list_jobs",
        "list_job_logs",
        "request_job_cancel",
        "migrate",
    ),
    "rbac": (
        "authorize",
        "simulate",
        "create_principal",
        "create_role",
        "bind_role",
        "migrate",
    ),
    "webhooks": (
        "create_destination",
        "update_destination",
        "disable_destination",
        "get_destination",
        "migrate",
    ),
    "audit": (
        "append_event",
        "get_event",
        "search_events",
        "export_jsonl",
        "export_csv",
        "export_digest",
        "migrate",
    ),
}


@dataclass(frozen=True, slots=True)
class StoreBundle:
    tokens: Any
    control: Any
    rbac: Any
    webhooks: Any
    audit: Any

    def __post_init__(self) -> None:
        stores = {
            "tokens": self.tokens,
            "control": self.control,
            "rbac": self.rbac,
            "webhooks": self.webhooks,
            "audit": self.audit,
        }
        connections = []
        for name, store in stores.items():
            connection = getattr(store, "connection", None)
            if not isinstance(connection, sqlite3.Connection):
                raise ConfigurationError(f"{name} store must expose a SQLite connection")
            connections.append(connection)
            for method in _REQUIRED_METHODS[name]:
                if not callable(getattr(store, method, None)):
                    raise ConfigurationError(f"{name} store is missing required method: {method}")
        first = connections[0]
        if any(connection is not first for connection in connections[1:]):
            raise ConfigurationError("all integration stores must share one SQLite connection")
        first.execute("PRAGMA foreign_keys = ON")
        if first.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            raise ConfigurationError("SQLite foreign-key enforcement must be enabled")
        first.execute("PRAGMA trusted_schema = OFF")
        trusted = first.execute("PRAGMA trusted_schema").fetchone()
        if trusted is None or trusted[0] != 0:
            raise ConfigurationError("SQLite trusted_schema must be disabled")

    @property
    def connection(self) -> sqlite3.Connection:
        return self.tokens.connection

    @contextlib.contextmanager
    def atomic(self) -> Iterator[None]:
        savepoint = "hp_api_integration_" + secrets.token_hex(8)
        self.connection.execute(f"SAVEPOINT {savepoint}")
        try:
            yield
        except BaseException:
            self.connection.execute(f"ROLLBACK TO {savepoint}")
            self.connection.execute(f"RELEASE {savepoint}")
            raise
        else:
            self.connection.execute(f"RELEASE {savepoint}")

    def migrate_all(self) -> None:
        """Migrate every foundation atomically and preserve caller transactions."""
        with self.atomic():
            self.tokens.migrate()
            self.control.migrate()
            self.rbac.migrate()
            self.webhooks.migrate()
            self.audit.migrate()

    def verify_compatibility(self) -> None:
        """Re-run non-mutating schema checks exposed by the foundations."""
        for store in (self.webhooks, self.audit):
            verifier = getattr(store, "verify_schema", None)
            if callable(verifier):
                verifier()
        with self.atomic():
            self.tokens.migrate()
            self.control.migrate()
            self.rbac.migrate()
