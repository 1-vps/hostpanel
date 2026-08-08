from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from hostpanel_api_tokens import (
    Actor,
    ApiTokenStore,
    AuthenticationError,
    AuthorizationError,
    ValidationError,
)


NOW = 1_800_000_000
ACTOR = Actor("system", "test-suite")


class ApiTokenStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.store = ApiTokenStore(self.connection)
        self.store.migrate()

    def tearDown(self) -> None:
        self.connection.close()

    def account(self, **overrides):
        values = {
            "name": "Automation",
            "scopes": ("sites:read", "sites:write", "backups:read"),
            "tenant_ids": ("tenant-a", "tenant-b"),
            "source_cidrs": ("192.0.2.0/24",),
            "actor": ACTOR,
            "now": NOW,
        }
        values.update(overrides)
        return self.store.create_service_account(**values)

    def token(self, account_id: str, **overrides):
        values = {
            "expires_at": NOW + 3600,
            "actor": ACTOR,
            "now": NOW,
        }
        values.update(overrides)
        return self.store.issue_token(account_id, **values)

    def test_migration_is_idempotent_and_foreign_keys_are_enforced(self) -> None:
        self.store.migrate()
        version, generation = self.connection.execute(
            "SELECT schema_version, token_generation FROM hp_api_security_state"
        ).fetchone()
        self.assertEqual((version, generation), (1, 0))
        self.assertEqual(self.connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                "INSERT INTO hp_api_token_scopes(token_id, scope) VALUES('missing', 'sites:read')"
            )

    def test_issue_authenticate_and_never_store_plaintext_secret(self) -> None:
        account = self.account()
        issued = self.token(
            account.account_id,
            scopes=("sites:read",),
            tenant_ids=("tenant-a",),
            resources={"site": ("example.com",)},
            label="Deploy reader",
        )
        self.assertRegex(issued.token, r"^hpv1\.tok_[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]{43}$")
        secret_hash = self.connection.execute(
            "SELECT secret_hash FROM hp_api_tokens WHERE token_id = ?",
            (issued.metadata.token_id,),
        ).fetchone()[0]
        self.assertIsInstance(secret_hash, bytes)
        self.assertEqual(len(secret_hash), 32)
        dump = "\n".join(self.connection.iterdump())
        self.assertNotIn(issued.token, dump)
        self.assertNotIn(issued.token.rsplit(".", 1)[1], dump)

        principal = self.store.authenticate(
            issued.token,
            required_scope="sites:read",
            tenant_id="tenant-a",
            source_ip="192.0.2.25",
            resource_type="site",
            resource_id="example.com",
            now=NOW + 30,
        )
        self.assertEqual(principal.account_id, account.account_id)
        self.assertEqual(principal.token_id, issued.metadata.token_id)
        self.assertEqual(principal.scopes, frozenset({"sites:read"}))
        self.assertEqual(
            self.connection.execute(
                "SELECT last_used_at FROM hp_api_tokens WHERE token_id = ?",
                (issued.metadata.token_id,),
            ).fetchone()[0],
            NOW + 30,
        )
        listed = self.store.list_tokens(account.account_id)
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0].label, "Deploy reader")
        self.assertFalse(hasattr(listed[0], "token"))

    def test_wrong_or_malformed_tokens_fail_with_the_same_public_error(self) -> None:
        account = self.account()
        issued = self.token(account.account_id)
        candidates = (
            "",
            "not-a-token",
            issued.token[:-1] + ("A" if issued.token[-1] != "A" else "B"),
            issued.token.replace("hpv1", "hpv2", 1),
        )
        for candidate in candidates:
            with self.subTest(candidate=candidate), self.assertRaisesRegex(
                AuthenticationError, "^invalid API token$"
            ):
                self.store.authenticate(
                    candidate,
                    required_scope="sites:read",
                    tenant_id="tenant-a",
                    source_ip="192.0.2.10",
                    now=NOW + 1,
                    touch=False,
                )

    def test_scope_tenant_resource_and_source_are_all_enforced(self) -> None:
        account = self.account()
        issued = self.token(
            account.account_id,
            scopes=("sites:read",),
            tenant_ids=("tenant-a",),
            source_cidrs=("192.0.2.0/28",),
            resources={"site": ("allowed.example",)},
        )
        authorization_cases = (
            {"required_scope": "sites:write", "tenant_id": "tenant-a", "resource_id": "allowed.example"},
            {"required_scope": "sites:read", "tenant_id": "tenant-b", "resource_id": "allowed.example"},
            {"required_scope": "sites:read", "tenant_id": "tenant-a", "resource_id": "denied.example"},
        )
        for case in authorization_cases:
            with self.subTest(case=case), self.assertRaises(AuthorizationError):
                self.store.authenticate(
                    issued.token,
                    source_ip="192.0.2.5",
                    resource_type="site",
                    now=NOW + 1,
                    touch=False,
                    **case,
                )
        for source in ("192.0.2.20", "198.51.100.1", None, "invalid"):
            with self.subTest(source=source), self.assertRaises(AuthenticationError):
                self.store.authenticate(
                    issued.token,
                    required_scope="sites:read",
                    tenant_id="tenant-a",
                    source_ip=source,
                    resource_type="site",
                    resource_id="allowed.example",
                    now=NOW + 1,
                    touch=False,
                )

    def test_issue_cannot_expand_account_grants(self) -> None:
        account = self.account(expires_at=NOW + 7200)
        invalid = (
            {"scopes": ("users:write",)},
            {"tenant_ids": ("tenant-c",)},
            {"allow_all_tenants": True},
            {"source_cidrs": ("192.0.0.0/16",)},
            {"expires_at": NOW + 7201},
        )
        for options in invalid:
            with self.subTest(options=options), self.assertRaises(ValidationError):
                self.token(account.account_id, **options)
        self.assertEqual(self.store.list_tokens(account.account_id), ())

    def test_expiry_revocation_disable_and_global_revoke_fail_closed(self) -> None:
        account = self.account()
        expired = self.token(account.account_id, expires_at=NOW + 10)
        with self.assertRaises(AuthenticationError):
            self.store.authenticate(
                expired.token,
                required_scope="sites:read",
                tenant_id="tenant-a",
                source_ip="192.0.2.1",
                now=NOW + 10,
                touch=False,
            )

        revoked = self.token(account.account_id, expires_at=NOW + 100)
        self.store.revoke_token(revoked.metadata.token_id, actor=ACTOR, now=NOW + 1)
        with self.assertRaises(AuthenticationError):
            self.store.authenticate(
                revoked.token,
                required_scope="sites:read",
                tenant_id="tenant-a",
                source_ip="192.0.2.1",
                now=NOW + 2,
                touch=False,
            )

        disabled = self.token(account.account_id, expires_at=NOW + 100)
        self.store.set_service_account_enabled(account.account_id, False, actor=ACTOR, now=NOW + 1)
        with self.assertRaises(AuthenticationError):
            self.store.authenticate(
                disabled.token,
                required_scope="sites:read",
                tenant_id="tenant-a",
                source_ip="192.0.2.1",
                now=NOW + 2,
                touch=False,
            )
        self.store.set_service_account_enabled(account.account_id, True, actor=ACTOR, now=NOW + 3)
        self.store.revoke_all_tokens(actor=ACTOR, now=NOW + 4)
        with self.assertRaises(AuthenticationError):
            self.store.authenticate(
                disabled.token,
                required_scope="sites:read",
                tenant_id="tenant-a",
                source_ip="192.0.2.1",
                now=NOW + 5,
                touch=False,
            )

    def test_lifecycle_audit_is_append_only_and_contains_no_secret(self) -> None:
        account = self.account()
        issued = self.token(account.account_id)
        self.store.revoke_token(issued.metadata.token_id, actor=ACTOR, now=NOW + 1)
        self.store.revoke_all_tokens(actor=ACTOR, now=NOW + 2)
        events = self.store.list_audit_events(limit=20)
        self.assertEqual(
            [event.action for event in events],
            [
                "service_account.created",
                "token.issued",
                "token.revoked",
                "token.global_revoke",
            ],
        )
        serialized = repr(events)
        self.assertNotIn(issued.token, serialized)
        self.assertNotIn(issued.token.rsplit(".", 1)[1], serialized)
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                "UPDATE hp_api_token_audit_events SET action = 'tampered' WHERE sequence = 1"
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute("DELETE FROM hp_api_token_audit_events WHERE sequence = 1")

    def test_token_and_audit_publication_are_one_transaction(self) -> None:
        account = self.account()
        self.connection.execute(
            """
            CREATE TRIGGER reject_token_audit
            BEFORE INSERT ON hp_api_token_audit_events
            WHEN NEW.action = 'token.issued'
            BEGIN
              SELECT RAISE(ABORT, 'injected audit failure');
            END
            """
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.token(account.account_id)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM hp_api_tokens").fetchone()[0], 0)
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM hp_api_token_audit_events WHERE action = 'token.issued'"
            ).fetchone()[0],
            0,
        )

    def test_outer_transaction_can_roll_back_account_and_audit(self) -> None:
        self.connection.execute("BEGIN")
        account = self.account()
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM hp_api_service_accounts WHERE account_id = ?",
                (account.account_id,),
            ).fetchone()[0],
            1,
        )
        self.connection.rollback()
        self.assertEqual(self.connection.execute("SELECT count(*) FROM hp_api_service_accounts").fetchone()[0], 0)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM hp_api_token_audit_events").fetchone()[0], 0)

    def test_all_tenant_and_cidr_inheritance_are_explicit(self) -> None:
        account = self.account(
            tenant_ids=(),
            allow_all_tenants=True,
            source_cidrs=("2001:db8::/32",),
        )
        issued = self.token(account.account_id)
        self.assertTrue(issued.metadata.allow_all_tenants)
        self.assertEqual(issued.metadata.tenant_ids, ())
        self.assertEqual(issued.metadata.source_cidrs, ("2001:db8::/32",))
        principal = self.store.authenticate(
            issued.token,
            required_scope="sites:read",
            tenant_id="tenant-anywhere",
            source_ip="2001:db8::1234",
            now=NOW + 1,
            touch=False,
        )
        self.assertTrue(principal.allow_all_tenants)

    def test_ambiguous_iterables_and_tenant_modes_are_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self.account(scopes="sites:read")
        with self.assertRaises(ValidationError):
            self.account(tenant_ids="tenant-a")
        with self.assertRaises(ValidationError):
            self.account(source_cidrs="192.0.2.0/24")
        with self.assertRaises(ValidationError):
            self.account(tenant_ids=("tenant-a",), allow_all_tenants=True)
        account = self.account()
        with self.assertRaises(ValidationError):
            self.token(
                account.account_id,
                tenant_ids=("tenant-a",),
                allow_all_tenants=True,
            )
        with self.assertRaises(ValidationError):
            self.token(account.account_id, resources={"site": "example.com"})

    def test_audit_cursor_is_stable_and_bounded(self) -> None:
        account = self.account()
        self.token(account.account_id)
        first = self.store.list_audit_events(limit=1)
        second = self.store.list_audit_events(after_sequence=first[0].sequence, limit=10)
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 1)
        self.assertGreater(second[0].sequence, first[0].sequence)
        for invalid in (0, 1001, -1, True):
            with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                self.store.list_audit_events(limit=invalid)


    def test_schema_shape_and_trusted_schema_are_fail_closed(self) -> None:
        self.assertEqual(self.connection.execute("PRAGMA trusted_schema").fetchone()[0], 0)
        self.connection.execute("DROP TRIGGER hp_api_token_audit_events_no_delete")
        self.connection.execute(
            """
            CREATE TRIGGER hp_api_token_audit_events_no_delete
            BEFORE DELETE ON hp_api_token_audit_events
            BEGIN
              SELECT 1;
            END
            """
        )
        with self.assertRaisesRegex(ValidationError, "unexpected HostPanel API schema object"):
            self.store.migrate()

    def test_cross_family_cidr_and_empty_resource_restrictions_are_rejected(self) -> None:
        account = self.account()
        with self.assertRaises(ValidationError):
            self.token(account.account_id, source_cidrs=("2001:db8::/64",))
        with self.assertRaises(ValidationError):
            self.token(account.account_id, resources={"site": ()})

    def test_token_listing_is_bounded_and_cursor_stable(self) -> None:
        account = self.account()
        first = self.token(account.account_id, label="First")
        second = self.token(account.account_id, label="Second")
        page_one = self.store.list_tokens(account.account_id, limit=1)
        self.assertEqual(len(page_one), 1)
        cursor = (page_one[0].created_at, page_one[0].token_id)
        page_two = self.store.list_tokens(account.account_id, after=cursor, limit=1)
        self.assertEqual(len(page_two), 1)
        self.assertNotEqual(page_one[0].token_id, page_two[0].token_id)
        self.assertEqual(
            {page_one[0].token_id, page_two[0].token_id},
            {first.metadata.token_id, second.metadata.token_id},
        )
        for invalid in (0, 1001, -1, True):
            with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                self.store.list_tokens(account.account_id, limit=invalid)
        with self.assertRaises(ValidationError):
            self.store.list_tokens(account.account_id, after=(NOW, "not-a-token"))

    def test_grant_counts_and_touch_flag_are_bounded(self) -> None:
        with self.assertRaisesRegex(ValidationError, "too many scopes"):
            self.account(scopes=tuple(f"resource{i}:read" for i in range(129)))
        account = self.account()
        issued = self.token(account.account_id)
        with self.assertRaisesRegex(ValidationError, "touch must be boolean"):
            self.store.authenticate(
                issued.token,
                required_scope="sites:read",
                tenant_id="tenant-a",
                source_ip="192.0.2.1",
                now=NOW + 1,
                touch=1,
            )


if __name__ == "__main__":
    unittest.main()
