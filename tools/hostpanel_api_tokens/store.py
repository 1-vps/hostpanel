from __future__ import annotations

import hmac

from .accounts import AccountMixin
from .base import StoreBase
from .model import (
    DUMMY_HASH,
    MAX_TOKEN_PAGE,
    AuthenticationError,
    Principal,
    identifier,
    now as normalize_now,
)
from .schema import atomic
from .tokens import TokenMixin


class ApiTokenStore(TokenMixin, AccountMixin, StoreBase):
    """Transactional service-account and API-token store."""

    def authenticate_identity(
        self,
        raw_token: str,
        *,
        source_ip: str | None,
        now: int | None = None,
        touch: bool = False,
    ) -> Principal:
        """Authenticate credential/account/source state before target lookup.

        Scope, tenant, and resource authorization are deliberately deferred to
        authenticate() after a trusted target resolver has read persistent state.
        """
        timestamp = normalize_now(now)
        if not isinstance(touch, bool):
            from .model import ValidationError

            raise ValidationError("touch must be boolean")
        with atomic(self.connection):
            try:
                token_id, candidate_hash = self._parse_token(raw_token)
            except AuthenticationError:
                hmac.compare_digest(DUMMY_HASH, DUMMY_HASH)
                raise
            row = self.connection.execute(
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
                or token_expires <= timestamp
                or (account_expires is not None and account_expires <= timestamp)
                or token_generation != current_generation
            ):
                raise AuthenticationError("invalid API token")
            account_cidrs = self._values(
                "hp_api_account_cidrs", "cidr", "account_id", account_id
            )
            token_cidrs = self._values(
                "hp_api_token_cidrs", "cidr", "token_id", token_id
            )
            self._authorize_source(source_ip, account_cidrs, token_cidrs)
            token_scopes = frozenset(
                self._values(
                    "hp_api_token_scopes", "scope", "token_id", token_id
                )
            )
            token_tenants = frozenset(
                self._values(
                    "hp_api_token_tenants", "tenant_id", "token_id", token_id
                )
            )
            if touch:
                self.connection.execute(
                    """
                    UPDATE hp_api_tokens
                    SET last_used_at = CASE
                        WHEN last_used_at IS NULL OR last_used_at < ? THEN ?
                        ELSE last_used_at
                    END
                    WHERE token_id = ?
                    """,
                    (timestamp, timestamp, token_id),
                )
            return Principal(
                account_id,
                token_id,
                token_scopes,
                token_tenants,
                bool(token_all_tenants),
            )

    def authenticate(
        self,
        raw_token: str,
        *,
        required_scope: str,
        tenant_id: str,
        source_ip: str | None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        now: int | None = None,
        touch: bool = True,
    ) -> Principal:
        with atomic(self.connection):
            return super().authenticate(
                raw_token,
                required_scope=required_scope,
                tenant_id=tenant_id,
                source_ip=source_ip,
                resource_type=resource_type,
                resource_id=resource_id,
                now=now,
                touch=touch,
            )

    def get_service_account(self, account_id: str):
        """Read one validated service account through a public contract."""
        return self._load_account(account_id)

    def list_service_accounts(
        self,
        *,
        tenant_id: str,
        after: tuple[int, str] | None = None,
        limit: int = 100,
    ) -> tuple:
        """List accounts visible in one tenant with a stable cursor."""
        tenant = identifier(tenant_id, "tenant ID")
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or not 1 <= limit <= MAX_TOKEN_PAGE
        ):
            from .model import ValidationError

            raise ValidationError("service-account page size is invalid")
        if after is None:
            cursor_created, cursor_account = -1, ""
        else:
            if not isinstance(after, tuple) or len(after) != 2:
                from .model import ValidationError

                raise ValidationError("service-account cursor is invalid")
            cursor_created = normalize_now(after[0])
            cursor_account = identifier(after[1], "service-account cursor ID")
        rows = self.connection.execute(
            """
            SELECT a.account_id
            FROM hp_api_service_accounts AS a
            WHERE (
              a.allow_all_tenants = 1
              OR EXISTS (
                SELECT 1 FROM hp_api_account_tenants AS t
                WHERE t.account_id = a.account_id AND t.tenant_id = ?
              )
            )
              AND (a.created_at > ? OR (a.created_at = ? AND a.account_id > ?))
            ORDER BY a.created_at, a.account_id
            LIMIT ?
            """,
            (tenant, cursor_created, cursor_created, cursor_account, limit),
        ).fetchall()
        return tuple(self._load_account(row[0]) for row in rows)
