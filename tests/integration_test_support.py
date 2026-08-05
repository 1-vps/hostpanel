from __future__ import annotations

import json
import sqlite3
from typing import Any

from integration_test_models import *
from integration_test_fakes import *


class TestStack:
    def __init__(self) -> None:
        install_domain_stubs()
        self.connection = sqlite3.connect(":memory:")
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA trusted_schema = OFF")
        self.trace: list[str] = []
        self.tokens = FakeTokenStore(self.connection, self.trace)
        self.control = FakeControlStore(self.connection, self.trace)
        self.rbac = FakeRbacStore(self.connection, self.trace)
        self.webhooks = FakeWebhookStore(self.connection)
        self.audit = FakeAuditStore(self.connection)
        for store in (self.tokens, self.control, self.rbac, self.webhooks, self.audit):
            store.migrate()
        self.connection.commit()
        # Credential principal exists for the protected route tests.
        self.connection.execute("INSERT INTO hp_api_principals VALUES(?, 1)", (self.tokens.identity.account_id,))
        self.connection.commit()

    def close(self) -> None:
        try:
            self.connection.close()
        except sqlite3.ProgrammingError:
            pass

    def bundle(self):
        from hostpanel_api_integration import StoreBundle
        return StoreBundle(self.tokens, self.control, self.rbac, self.webhooks, self.audit)

    def app(self):
        from hostpanel_api_integration import ManagementApi
        return ManagementApi(
            stores=self.bundle(),
            tenant_exists=lambda tenant: tenant in {"tenant-a", "tenant-b"},
            cursor_secret=b"c" * 32,
            rate_limiter=FakeRateLimiter(),
            allowed_job_types={"site.backup", "site.deploy"},
        )


def request(method: str, path: str, *, body: Any = None, query: str = "", token: str = "good-token-0000000000", idem: str | None = None, request_id: str = "request-1234"):
    from hostpanel_api_http import Request
    headers: list[tuple[str, str]] = []
    if token:
        headers.append(("Authorization", "Bearer " + token))
    if request_id:
        headers.append(("X-Request-Id", request_id))
    raw = b""
    if body is not None:
        raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        headers.append(("Content-Type", "application/json"))
    if idem is not None:
        headers.append(("Idempotency-Key", idem))
    return Request(method=method, raw_path=path, query_string=query, headers=tuple(headers), body=raw, source_ip="203.0.113.10", scheme="https", host="panel.example")


def decode(response):
    return json.loads(response.body.decode()) if response.body else None
