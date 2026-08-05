from __future__ import annotations

import hmac
from typing import Any

from .errors import ConfigurationError


class TokenIdentityAuthenticator:
    """Authenticate a token before any tenant/resource target is resolved.

    Newer token stores may expose authenticate_identity(). For the reviewed v1
    token store, this adapter performs the exact credential/account/generation/
    source checks over the same SQLite snapshot and deliberately omits only the
    target-dependent scope, tenant, and resource checks. Those are re-run by the
    public full authenticate() call after trusted target resolution.
    """

    def __init__(self, token_store: Any) -> None:
        self.store = token_store

    def authenticate(self, raw_token: str, source_ip: str, now: int):
        public = getattr(self.store, "authenticate_identity", None)
        if callable(public):
            return public(raw_token, source_ip=source_ip, now=now, touch=False)
        return self._authenticate_v1(raw_token, source_ip=source_ip, now=now)

    def _authenticate_v1(self, raw_token: str, *, source_ip: str, now: int):
        try:
            from hostpanel_api_tokens.model import (
                DUMMY_HASH,
                AuthenticationError,
                Principal,
            )
        except ImportError as exc:  # pragma: no cover - configuration guard
            raise ConfigurationError("hostpanel_api_tokens is unavailable") from exc
        parser = getattr(self.store, "_parse_token", None)
        values = getattr(self.store, "_values", None)
        authorize_source = getattr(self.store, "_authorize_source", None)
        if not callable(parser) or not callable(values) or not callable(authorize_source):
            raise ConfigurationError("token store identity adapter contract changed")
        try:
            token_id, candidate_hash = parser(raw_token)
        except AuthenticationError:
            hmac.compare_digest(DUMMY_HASH, DUMMY_HASH)
            raise
        row = self.store.connection.execute(
            """
            SELECT t.account_id, t.secret_hash, t.generation, t.expires_at,
                   t.revoked_at, t.allow_all_tenants, a.enabled, a.expires_at,
                   s.token_generation
            FROM hp_api_tokens AS t
            JOIN hp_api_service_accounts AS a ON a.account_id = t.account_id
            JOIN hp_api_security_state AS s ON s.singleton = 1
            WHERE t.token_id = ?
            """,
            (token_id,),
        ).fetchone()
        stored_hash = DUMMY_HASH if row is None else bytes(row[1])
        if row is None or not hmac.compare_digest(stored_hash, candidate_hash):
            raise AuthenticationError("invalid API token")
        (
            account_id,
            _stored_hash,
            token_generation,
            token_expires,
            revoked_at,
            token_all_tenants,
            account_enabled,
            account_expires,
            current_generation,
        ) = row
        if (
            not account_enabled
            or revoked_at is not None
            or token_expires <= now
            or (account_expires is not None and account_expires <= now)
            or token_generation != current_generation
        ):
            raise AuthenticationError("invalid API token")
        account_cidrs = values("hp_api_account_cidrs", "cidr", "account_id", account_id)
        token_cidrs = values("hp_api_token_cidrs", "cidr", "token_id", token_id)
        authorize_source(source_ip, account_cidrs, token_cidrs)
        token_scopes = frozenset(values("hp_api_token_scopes", "scope", "token_id", token_id))
        token_tenants = frozenset(values("hp_api_token_tenants", "tenant_id", "token_id", token_id))
        return Principal(
            account_id,
            token_id,
            token_scopes,
            token_tenants,
            bool(token_all_tenants),
        )
