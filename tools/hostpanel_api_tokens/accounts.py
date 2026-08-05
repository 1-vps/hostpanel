from __future__ import annotations

from collections.abc import Iterable

from .model import (
    ACCOUNT_ID_RE,
    Actor,
    ServiceAccount,
    ValidationError,
    actor as normalize_actor,
    cidrs,
    clean_text,
    encode_random,
    identifier,
    identifiers,
    now as normalize_now,
    scopes as normalize_scopes,
)
from .schema import atomic


class AccountMixin:
    def create_service_account(
        self,
        *,
        name: str,
        scopes: Iterable[str],
        tenant_ids: Iterable[str] = (),
        allow_all_tenants: bool = False,
        source_cidrs: Iterable[str] = (),
        expires_at: int | None = None,
        actor: Actor,
        now: int | None = None,
        account_id: str | None = None,
    ) -> ServiceAccount:
        timestamp = normalize_now(now)
        event_actor = normalize_actor(actor)
        account_name = clean_text(name, "service-account name")
        grants = normalize_scopes(scopes)
        tenants = identifiers(tenant_ids, "tenant ID")
        networks = cidrs(source_cidrs)
        if not isinstance(allow_all_tenants, bool):
            raise ValidationError("allow_all_tenants must be boolean")
        if allow_all_tenants and tenants:
            raise ValidationError("all-tenant service accounts cannot also list tenants")
        if not allow_all_tenants and not tenants:
            raise ValidationError(
                "service account must grant explicit tenants or all tenants"
            )
        if expires_at is not None:
            expires_at = normalize_now(expires_at)
            if expires_at <= timestamp:
                raise ValidationError("service-account expiry must be in the future")
        generated_id = "sa_" + encode_random(16)
        account_identifier = generated_id if account_id is None else account_id
        if (
            not isinstance(account_identifier, str)
            or ACCOUNT_ID_RE.fullmatch(account_identifier) is None
        ):
            raise ValidationError("service-account ID is invalid")

        with atomic(self.connection):
            self.connection.execute(
                """
                INSERT INTO hp_api_service_accounts(
                    account_id, name, enabled, allow_all_tenants,
                    created_at, expires_at
                ) VALUES(?, ?, 1, ?, ?, ?)
                """,
                (
                    account_identifier,
                    account_name,
                    int(allow_all_tenants),
                    timestamp,
                    expires_at,
                ),
            )
            self.connection.executemany(
                "INSERT INTO hp_api_account_scopes(account_id, scope) VALUES(?, ?)",
                ((account_identifier, value) for value in grants),
            )
            self.connection.executemany(
                "INSERT INTO hp_api_account_tenants(account_id, tenant_id) VALUES(?, ?)",
                ((account_identifier, value) for value in tenants),
            )
            self.connection.executemany(
                "INSERT INTO hp_api_account_cidrs(account_id, cidr) VALUES(?, ?)",
                ((account_identifier, value) for value in networks),
            )
            self._audit(
                timestamp,
                event_actor,
                "service_account.created",
                "service_account",
                account_identifier,
                {
                    "allow_all_tenants": allow_all_tenants,
                    "expires_at": expires_at,
                    "scope_count": len(grants),
                    "tenant_count": len(tenants),
                    "source_cidr_count": len(networks),
                },
            )
        return ServiceAccount(
            account_identifier,
            account_name,
            True,
            grants,
            tenants,
            allow_all_tenants,
            networks,
            timestamp,
            expires_at,
        )

    def set_service_account_enabled(
        self,
        account_id: str,
        enabled: bool,
        *,
        actor: Actor,
        now: int | None = None,
    ) -> None:
        account_identifier = identifier(account_id, "service-account ID")
        if (
            ACCOUNT_ID_RE.fullmatch(account_identifier) is None
            or not isinstance(enabled, bool)
        ):
            raise ValidationError("service-account state is invalid")
        timestamp = normalize_now(now)
        event_actor = normalize_actor(actor)
        with atomic(self.connection):
            result = self.connection.execute(
                "UPDATE hp_api_service_accounts SET enabled = ? WHERE account_id = ?",
                (int(enabled), account_identifier),
            )
            if result.rowcount != 1:
                raise ValidationError("service account does not exist")
            self._audit(
                timestamp,
                event_actor,
                "service_account.enabled" if enabled else "service_account.disabled",
                "service_account",
                account_identifier,
                {},
            )
