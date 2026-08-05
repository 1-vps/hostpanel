from __future__ import annotations

import base64
import hmac
import ipaddress
import secrets
from collections.abc import Iterable, Mapping

from .model import (
    ACCOUNT_ID_RE,
    DUMMY_HASH,
    HASH_ALGORITHM,
    MAX_TOKEN_PAGE,
    TOKEN_ID_RE,
    TOKEN_PREFIX,
    Actor,
    AuthenticationError,
    AuthorizationError,
    IssuedToken,
    Principal,
    TokenMetadata,
    ValidationError,
    actor as normalize_actor,
    cidrs,
    clean_text,
    decode_secret,
    encode_random,
    expiry,
    identifier,
    identifiers,
    now as normalize_now,
    resources as normalize_resources,
    scope as normalize_scope,
    scopes as normalize_scopes,
    secret_hash,
)
from .schema import atomic


class TokenMixin:
    def issue_token(
        self,
        account_id: str,
        *,
        expires_at: int,
        actor: Actor,
        label: str = "API token",
        scopes: Iterable[str] | None = None,
        tenant_ids: Iterable[str] | None = None,
        allow_all_tenants: bool | None = None,
        source_cidrs: Iterable[str] | None = None,
        resources: Mapping[str, Iterable[str]] | None = None,
        now: int | None = None,
    ) -> IssuedToken:
        timestamp = normalize_now(now)
        token_expiry = expiry(timestamp, expires_at)
        event_actor = normalize_actor(actor)
        account_identifier = identifier(account_id, "service-account ID")
        if ACCOUNT_ID_RE.fullmatch(account_identifier) is None:
            raise ValidationError("service-account ID is invalid")
        token_label = clean_text(label, "token label")
        resource_grants = normalize_resources(resources)

        with atomic(self.connection):
            account = self._load_account(account_identifier)
            if not account.enabled or (
                account.expires_at is not None and account.expires_at <= timestamp
            ):
                raise ValidationError("service account is not active")
            if account.expires_at is not None and token_expiry > account.expires_at:
                raise ValidationError(
                    "token expiry exceeds the service-account expiry"
                )

            requested_scopes = (
                account.scopes if scopes is None else normalize_scopes(scopes)
            )
            if not set(requested_scopes).issubset(account.scopes):
                raise ValidationError(
                    "token scopes exceed the service-account grant"
                )

            if allow_all_tenants is None and tenant_ids is None:
                token_all_tenants = account.allow_all_tenants
                requested_tenants = account.tenant_ids
            else:
                if allow_all_tenants is not None and not isinstance(
                    allow_all_tenants, bool
                ):
                    raise ValidationError("allow_all_tenants must be boolean")
                token_all_tenants = bool(allow_all_tenants)
                requested_tenants = (
                    ()
                    if tenant_ids is None
                    else identifiers(tenant_ids, "tenant ID")
                )
            if token_all_tenants and requested_tenants:
                raise ValidationError("all-tenant tokens cannot also list tenants")
            if token_all_tenants and not account.allow_all_tenants:
                raise ValidationError(
                    "token tenant grant exceeds the service-account grant"
                )
            if not token_all_tenants:
                if not requested_tenants:
                    raise ValidationError(
                        "token must grant explicit tenants or all tenants"
                    )
                if (
                    not account.allow_all_tenants
                    and not set(requested_tenants).issubset(account.tenant_ids)
                ):
                    raise ValidationError(
                        "token tenants exceed the service-account grant"
                    )

            requested_cidrs = (
                account.source_cidrs
                if source_cidrs is None
                else cidrs(source_cidrs)
            )
            if account.source_cidrs:
                if not requested_cidrs:
                    raise ValidationError(
                        "token cannot remove the service-account source restriction"
                    )
                account_networks = tuple(
                    ipaddress.ip_network(value) for value in account.source_cidrs
                )
                for value in requested_cidrs:
                    network = ipaddress.ip_network(value)
                    if not any(
                        network.version == parent.version
                        and network.subnet_of(parent)
                        for parent in account_networks
                    ):
                        raise ValidationError(
                            "token source CIDR exceeds the service-account grant"
                        )

            token_id = "tok_" + encode_random(16)
            secret = secrets.token_bytes(32)
            encoded_secret = (
                base64.urlsafe_b64encode(secret).rstrip(b"=").decode("ascii")
            )
            raw_token = f"{TOKEN_PREFIX}.{token_id}.{encoded_secret}"
            generation = self.connection.execute(
                "SELECT token_generation FROM hp_api_security_state WHERE singleton = 1"
            ).fetchone()[0]
            self.connection.execute(
                """
                INSERT INTO hp_api_tokens(
                    token_id, account_id, secret_hash, hash_algorithm,
                    generation, label, allow_all_tenants, created_at, expires_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    token_id,
                    account_identifier,
                    secret_hash(secret),
                    HASH_ALGORITHM,
                    generation,
                    token_label,
                    int(token_all_tenants),
                    timestamp,
                    token_expiry,
                ),
            )
            self.connection.executemany(
                "INSERT INTO hp_api_token_scopes(token_id, scope) VALUES(?, ?)",
                ((token_id, value) for value in requested_scopes),
            )
            self.connection.executemany(
                "INSERT INTO hp_api_token_tenants(token_id, tenant_id) VALUES(?, ?)",
                ((token_id, value) for value in requested_tenants),
            )
            self.connection.executemany(
                "INSERT INTO hp_api_token_cidrs(token_id, cidr) VALUES(?, ?)",
                ((token_id, value) for value in requested_cidrs),
            )
            self.connection.executemany(
                "INSERT INTO hp_api_token_resources(token_id, resource_type, resource_id) VALUES(?, ?, ?)",
                (
                    (token_id, resource_type, resource_id)
                    for resource_type, resource_id in resource_grants
                ),
            )
            self._audit(
                timestamp,
                event_actor,
                "token.issued",
                "api_token",
                token_id,
                {
                    "account_id": account_identifier,
                    "allow_all_tenants": token_all_tenants,
                    "expires_at": token_expiry,
                    "resource_count": len(resource_grants),
                    "scope_count": len(requested_scopes),
                    "source_cidr_count": len(requested_cidrs),
                    "tenant_count": len(requested_tenants),
                },
            )
            metadata = TokenMetadata(
                token_id,
                account_identifier,
                token_label,
                requested_scopes,
                requested_tenants,
                token_all_tenants,
                requested_cidrs,
                resource_grants,
                timestamp,
                token_expiry,
                None,
                None,
                generation,
            )
        return IssuedToken(raw_token, metadata)

    def revoke_token(
        self,
        token_id: str,
        *,
        actor: Actor,
        now: int | None = None,
    ) -> None:
        token_identifier = identifier(token_id, "token ID")
        if TOKEN_ID_RE.fullmatch(token_identifier) is None:
            raise ValidationError("token ID is invalid")
        timestamp = normalize_now(now)
        event_actor = normalize_actor(actor)
        with atomic(self.connection):
            result = self.connection.execute(
                "UPDATE hp_api_tokens SET revoked_at = ? WHERE token_id = ? AND revoked_at IS NULL",
                (timestamp, token_identifier),
            )
            if result.rowcount != 1:
                raise ValidationError(
                    "token does not exist or is already revoked"
                )
            self._audit(
                timestamp,
                event_actor,
                "token.revoked",
                "api_token",
                token_identifier,
                {},
            )

    def revoke_all_tokens(
        self,
        *,
        actor: Actor,
        now: int | None = None,
    ) -> int:
        timestamp = normalize_now(now)
        event_actor = normalize_actor(actor)
        with atomic(self.connection):
            self.connection.execute(
                "UPDATE hp_api_security_state SET token_generation = token_generation + 1 WHERE singleton = 1"
            )
            generation = self.connection.execute(
                "SELECT token_generation FROM hp_api_security_state WHERE singleton = 1"
            ).fetchone()[0]
            self._audit(
                timestamp,
                event_actor,
                "token.global_revoke",
                "api_security_state",
                "global",
                {"generation": generation},
            )
        return generation

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
        timestamp = normalize_now(now)
        required = normalize_scope(required_scope)
        tenant = identifier(tenant_id, "tenant ID")
        if (resource_type is None) != (resource_id is None):
            raise ValidationError(
                "resource type and resource ID must be supplied together"
            )
        kind = (
            identifier(resource_type, "resource type")
            if resource_type is not None
            else None
        )
        item = (
            identifier(resource_id, "resource ID")
            if resource_id is not None
            else None
        )
        if not isinstance(touch, bool):
            raise ValidationError("touch must be boolean")
        try:
            token_id, candidate_hash = self._parse_token(raw_token)
        except AuthenticationError:
            hmac.compare_digest(DUMMY_HASH, DUMMY_HASH)
            raise

        row = self.connection.execute(
            """
            SELECT
                t.account_id, t.secret_hash, t.generation, t.expires_at,
                t.revoked_at, t.allow_all_tenants, a.enabled, a.expires_at,
                a.allow_all_tenants, s.token_generation
            FROM hp_api_tokens AS t
            JOIN hp_api_service_accounts AS a ON a.account_id = t.account_id
            JOIN hp_api_security_state AS s ON s.singleton = 1
            WHERE t.token_id = ?
            """,
            (token_id,),
        ).fetchone()
        stored_hash = DUMMY_HASH if row is None else bytes(row[1])
        secret_matches = hmac.compare_digest(stored_hash, candidate_hash)
        if row is None or not secret_matches:
            raise AuthenticationError("invalid API token")
        (
            account_id,
            _stored_secret,
            token_generation,
            token_expires,
            revoked_at,
            token_all_tenants,
            account_enabled,
            account_expires,
            account_all_tenants,
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

        account_scopes = frozenset(
            self._values(
                "hp_api_account_scopes", "scope", "account_id", account_id
            )
        )
        token_scopes = frozenset(
            self._values(
                "hp_api_token_scopes", "scope", "token_id", token_id
            )
        )
        if required not in account_scopes or required not in token_scopes:
            raise AuthorizationError(
                "API token does not grant the required scope"
            )

        account_tenants = frozenset(
            self._values(
                "hp_api_account_tenants", "tenant_id", "account_id", account_id
            )
        )
        token_tenants = frozenset(
            self._values(
                "hp_api_token_tenants", "tenant_id", "token_id", token_id
            )
        )
        if (not account_all_tenants and tenant not in account_tenants) or (
            not token_all_tenants and tenant not in token_tenants
        ):
            raise AuthorizationError(
                "API token does not grant the requested tenant"
            )

        if kind is not None and item is not None:
            allowed_resources = frozenset(
                resource_row[0]
                for resource_row in self.connection.execute(
                    "SELECT resource_id FROM hp_api_token_resources WHERE token_id = ? AND resource_type = ?",
                    (token_id, kind),
                )
            )
            if allowed_resources and item not in allowed_resources:
                raise AuthorizationError(
                    "API token does not grant the requested resource"
                )

        if touch:
            with atomic(self.connection):
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

    def list_tokens(
        self,
        account_id: str,
        *,
        after: tuple[int, str] | None = None,
        limit: int = 100,
    ) -> tuple[TokenMetadata, ...]:
        account_identifier = identifier(account_id, "service-account ID")
        if ACCOUNT_ID_RE.fullmatch(account_identifier) is None:
            raise ValidationError("service-account ID is invalid")
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or not 1 <= limit <= MAX_TOKEN_PAGE
        ):
            raise ValidationError("token page size is invalid")
        if after is None:
            cursor_created, cursor_token = -1, ""
        else:
            if not isinstance(after, tuple) or len(after) != 2:
                raise ValidationError("token cursor is invalid")
            cursor_created = normalize_now(after[0])
            cursor_token = identifier(after[1], "token cursor ID")
            if TOKEN_ID_RE.fullmatch(cursor_token) is None:
                raise ValidationError("token cursor is invalid")
        rows = self.connection.execute(
            """
            SELECT token_id, label, allow_all_tenants, created_at,
                   expires_at, revoked_at, last_used_at, generation
            FROM hp_api_tokens
            WHERE account_id = ?
              AND (created_at > ? OR (created_at = ? AND token_id > ?))
            ORDER BY created_at, token_id
            LIMIT ?
            """,
            (
                account_identifier,
                cursor_created,
                cursor_created,
                cursor_token,
                limit,
            ),
        ).fetchall()
        return tuple(
            TokenMetadata(
                token_id,
                account_identifier,
                label,
                self._values(
                    "hp_api_token_scopes", "scope", "token_id", token_id
                ),
                self._values(
                    "hp_api_token_tenants", "tenant_id", "token_id", token_id
                ),
                bool(all_tenants),
                self._values(
                    "hp_api_token_cidrs", "cidr", "token_id", token_id
                ),
                tuple(
                    self.connection.execute(
                        """
                        SELECT resource_type, resource_id
                        FROM hp_api_token_resources
                        WHERE token_id = ?
                        ORDER BY resource_type, resource_id
                        """,
                        (token_id,),
                    )
                ),
                created_at,
                expires_at,
                revoked_at,
                last_used_at,
                generation,
            )
            for (
                token_id,
                label,
                all_tenants,
                created_at,
                expires_at,
                revoked_at,
                last_used_at,
                generation,
            ) in rows
        )

    def _authorize_source(
        self,
        source_ip: str | None,
        account_cidrs: tuple[str, ...],
        token_cidrs: tuple[str, ...],
    ) -> None:
        if not account_cidrs and not token_cidrs:
            return
        if not isinstance(source_ip, str):
            raise AuthenticationError("invalid API token")
        try:
            address = ipaddress.ip_address(source_ip)
        except ValueError as exc:
            raise AuthenticationError("invalid API token") from exc
        if account_cidrs and not any(
            address in ipaddress.ip_network(value) for value in account_cidrs
        ):
            raise AuthenticationError("invalid API token")
        if token_cidrs and not any(
            address in ipaddress.ip_network(value) for value in token_cidrs
        ):
            raise AuthenticationError("invalid API token")

    def _parse_token(self, raw_token: str) -> tuple[str, bytes]:
        if not isinstance(raw_token, str) or len(raw_token) > 128:
            raise AuthenticationError("invalid API token")
        pieces = raw_token.split(".")
        if (
            len(pieces) != 3
            or pieces[0] != TOKEN_PREFIX
            or TOKEN_ID_RE.fullmatch(pieces[1]) is None
        ):
            raise AuthenticationError("invalid API token")
        return pieces[1], secret_hash(decode_secret(pieces[2]))
