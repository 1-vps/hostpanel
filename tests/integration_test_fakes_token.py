from __future__ import annotations

import dataclasses
import hashlib
import io
import json
import sqlite3

from hostpanel_api_http import RateLimitDecision
from integration_test_models import *


class FakeRateLimiter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def consume(self, *, identity, operation_id, policy, now):
        self.calls.append((identity, operation_id))
        return RateLimitDecision(True, policy.limit, policy.limit - 1, now + policy.window_seconds, 0)


class FakeTokenStore:
    def __init__(self, connection: sqlite3.Connection, trace: list[str]) -> None:
        self.connection = connection
        self.trace = trace
        self.allowed_scopes: set[str] | None = None
        self.identity = Identity("sa_admin00000000000000000", "tok_admin0000000000000000")
        self.next_account = 1
        self.next_token = 1
        self.fail_create = False

    def migrate(self):
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS hp_api_service_accounts("
            "account_id TEXT PRIMARY KEY, name TEXT NOT NULL, enabled INTEGER NOT NULL, "
            "allow_all_tenants INTEGER NOT NULL, created_at INTEGER NOT NULL, expires_at INTEGER)"
        )
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS hp_api_account_tenants(account_id TEXT, tenant_id TEXT)"
        )
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS hp_api_tokens("
            "token_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, revoked_at INTEGER)"
        )

    def authenticate_identity(self, raw_token, *, source_ip, now, touch=False):
        self.trace.append("identity")
        if raw_token != "good-token-0000000000":
            raise type("AuthenticationError", (RuntimeError,), {})("invalid")
        return self.identity

    def authenticate(self, raw_token, *, required_scope, tenant_id, source_ip, resource_type=None, resource_id=None, now=None, touch=True):
        self.trace.append("token-authorize")
        if raw_token != "good-token-0000000000":
            raise type("AuthenticationError", (RuntimeError,), {})("invalid")
        if self.allowed_scopes is not None and required_scope not in self.allowed_scopes:
            raise type("AuthorizationError", (RuntimeError,), {})("denied")
        return self.identity

    def create_service_account(self, *, name, scopes, tenant_ids=(), allow_all_tenants=False, source_cidrs=(), expires_at=None, actor, now=None, account_id=None):
        if self.fail_create:
            raise type("ValidationError", (RuntimeError,), {})("forced")
        account_id = account_id or f"sa_test{self.next_account:018d}"
        self.next_account += 1
        self.connection.execute(
            "INSERT INTO hp_api_service_accounts VALUES(?, ?, 1, ?, ?, ?)",
            (account_id, name, int(allow_all_tenants), now, expires_at),
        )
        self.connection.executemany(
            "INSERT INTO hp_api_account_tenants VALUES(?, ?)",
            ((account_id, tenant) for tenant in tenant_ids),
        )
        return ServiceAccount(account_id, name, True, tuple(scopes), tuple(tenant_ids), allow_all_tenants, tuple(source_cidrs), now, expires_at)

    def get_service_account(self, account_id):
        return self._load_account(account_id)

    def _load_account(self, account_id):
        row = self.connection.execute(
            "SELECT name, enabled, allow_all_tenants, created_at, expires_at FROM hp_api_service_accounts WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        if row is None:
            raise type("ValidationError", (RuntimeError,), {})("does not exist")
        tenants = tuple(r[0] for r in self.connection.execute("SELECT tenant_id FROM hp_api_account_tenants WHERE account_id = ? ORDER BY tenant_id", (account_id,)))
        return ServiceAccount(account_id, row[0], bool(row[1]), (), tenants, bool(row[2]), (), row[3], row[4])

    def list_service_accounts(self, *, tenant_id, after=None, limit=100):
        rows = self.connection.execute(
            """
            SELECT a.account_id FROM hp_api_service_accounts AS a
            WHERE a.allow_all_tenants = 1 OR EXISTS(
              SELECT 1 FROM hp_api_account_tenants AS t
              WHERE t.account_id = a.account_id AND t.tenant_id = ?
            )
            ORDER BY a.created_at, a.account_id LIMIT ?
            """,
            (tenant_id, limit),
        ).fetchall()
        return tuple(self._load_account(row[0]) for row in rows)

    def set_service_account_enabled(self, account_id, enabled, *, actor, now=None):
        result = self.connection.execute("UPDATE hp_api_service_accounts SET enabled = ? WHERE account_id = ?", (int(enabled), account_id))
        if result.rowcount != 1:
            raise type("ValidationError", (RuntimeError,), {})("does not exist")

    def issue_token(self, account_id, *, expires_at, actor, label="API token", scopes=None, tenant_ids=None, allow_all_tenants=None, source_cidrs=None, resources=None, now=None):
        self._load_account(account_id)
        token_id = f"tok_test{self.next_token:017d}"
        self.next_token += 1
        self.connection.execute("INSERT INTO hp_api_tokens VALUES(?, ?, NULL)", (token_id, account_id))
        metadata = TokenMetadata(token_id, account_id, label, tuple(scopes or ("job:read",)), tuple(tenant_ids or ()), bool(allow_all_tenants), tuple(source_cidrs or ()), tuple((kind, item) for kind, values in (resources or {}).items() for item in values), now, expires_at, None, None, 1)
        return IssuedToken("one-time-" + token_id, metadata)

    def list_tokens(self, account_id, *, after=None, limit=100):
        rows = self.connection.execute("SELECT token_id, revoked_at FROM hp_api_tokens WHERE account_id = ? ORDER BY token_id", (account_id,)).fetchall()
        return tuple(TokenMetadata(row[0], account_id, "API token", (), (), False, (), (), 1, 9999999999, row[1], None, 1) for row in rows[:limit])

    def revoke_token(self, token_id, *, actor, now=None):
        result = self.connection.execute("UPDATE hp_api_tokens SET revoked_at = ? WHERE token_id = ? AND revoked_at IS NULL", (now, token_id))
        if result.rowcount != 1:
            raise type("ValidationError", (RuntimeError,), {})("does not exist or revoked")
