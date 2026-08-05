from __future__ import annotations

from tests.rbac_test_support import *  # noqa: F403


class HostpanelApiRbacSecurityTests(RbacTestCase):  # noqa: F405
    def test_migration_is_idempotent_exact_and_append_only(self) -> None:
        self.store.migrate()
        self.assertEqual(self.connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertEqual(self.connection.execute("PRAGMA trusted_schema").fetchone()[0], 0)
        self.principal("operator-a")
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                "UPDATE hp_api_rbac_audit_events SET action = 'tampered' WHERE sequence = 1"
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute("DELETE FROM hp_api_rbac_audit_events WHERE sequence = 1")
        self.connection.execute("DROP TRIGGER hp_api_rbac_audit_no_delete")
        self.connection.execute(
            """
            CREATE TRIGGER hp_api_rbac_audit_no_delete
            BEFORE DELETE ON hp_api_rbac_audit_events BEGIN SELECT 1; END
            """
        )
        with self.assertRaisesRegex(ValidationError, "unexpected HostPanel RBAC schema object"):
            self.store.migrate()

    def test_wildcards_overlap_and_ambiguous_iterables_are_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self.role("Wildcard", allows=("sites:*",))
        with self.assertRaises(ValidationError):
            self.role(
                "Overlap",
                allows=("sites:read",),
                denies=("sites:read",),
            )
        with self.assertRaises(ValidationError):
            self.role("String iterable", allows="sites:read")
        with self.assertRaises(ValidationError):
            self.role("Empty")

    def test_binding_scope_flags_and_expiry_are_fail_closed(self) -> None:
        self.principal("operator-a", expires_at=NOW + 100)
        role = self.role("Reader", allows=("sites:read",))
        invalid = (
            {"tenant_ids": ("tenant-a",), "allow_all_tenants": True},
            {"tenant_ids": (), "allow_all_tenants": False},
            {"resources": {"site": ("example.com",)}, "allow_all_resources": True},
            {"resources": None, "allow_all_resources": False},
            {"expires_at": NOW + 101},
        )
        for options in invalid:
            with self.subTest(options=options), self.assertRaises(ValidationError):
                self.bind("operator-a", role.role_id, **options)

    def test_policy_and_audit_respect_outer_transaction(self) -> None:
        self.connection.execute("BEGIN")
        principal = self.principal("operator-a")
        role = self.role("Reader", allows=("sites:read",))
        self.bind(principal.principal_id, role.role_id)
        self.assertTrue(
            self.store.simulate(
                principal_id="operator-a",
                capability="sites:read",
                tenant_id="tenant-a",
                now=NOW,
            ).allowed
        )
        self.connection.rollback()
        self.assertEqual(self.connection.execute("SELECT count(*) FROM hp_api_principals").fetchone()[0], 0)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM hp_api_roles").fetchone()[0], 0)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM hp_api_rbac_audit_events").fetchone()[0], 0)

    def test_audit_metadata_is_canonical_and_bounded(self) -> None:
        self.principal("operator-a")
        event = self.store.list_audit_events()[0]
        self.assertEqual(event.action, "principal.created")
        self.connection.execute(
            "UPDATE sqlite_sequence SET seq = seq WHERE name = 'hp_api_rbac_audit_events'"
        )
        self.connection.execute("DROP TRIGGER hp_api_rbac_audit_no_update")
        self.connection.execute(
            "UPDATE hp_api_rbac_audit_events SET metadata_json = ? WHERE sequence = ?",
            ('{"principal_kind": "operator", "expires_at": null}', event.sequence),
        )
        with self.assertRaisesRegex(ValidationError, "not canonical"):
            self.store.list_audit_events()

    def test_schema_rejects_impossible_impersonation_state(self) -> None:
        self.principal("operator-a")
        self.principal("customer-a", "customer")
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                """
                INSERT INTO hp_api_impersonation_sessions(
                    session_id, actor_principal_id, target_principal_id,
                    tenant_id, reason, created_at, expires_at, ended_at, end_reason
                ) VALUES(
                    'imp_abcdefghijklmnopqrstuv', 'operator-a', 'customer-a',
                    'tenant-a', 'Invalid terminal state', ?, ?, ?, NULL
                )
                """,
                (NOW, NOW + 60, NOW + 1),
            )

    def test_capability_and_audit_page_bounds(self) -> None:
        with self.assertRaisesRegex(ValidationError, "too many capabilities"):
            self.role(
                "Too many",
                allows=tuple(f"resource{i}:read" for i in range(513)),
            )
        self.principal("operator-a")
        for invalid in (0, 1001, True, -1):
            with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                self.store.list_audit_events(limit=invalid)

    def test_policy_match_count_is_bounded_and_fail_closed(self) -> None:
        self.principal("operator-a")
        role = self.role("Reader", allows=("sites:read",))
        rows = []
        for index in range(1001):
            rows.append(
                (
                    f"bind_direct_{index:04d}",
                    "operator-a",
                    role.role_id,
                    1,
                    1,
                    NOW,
                )
            )
        self.connection.executemany(
            """
            INSERT INTO hp_api_role_bindings(
                binding_id, principal_id, role_id, allow_all_tenants,
                allow_all_resources, created_at
            ) VALUES(?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        decision = self.store.simulate(
            principal_id="operator-a",
            capability="sites:read",
            tenant_id="tenant-a",
            now=NOW,
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "policy_too_complex")


    def test_time_invariants_fail_closed(self) -> None:
        self.principal("operator-a")
        role = self.role("Reader", allows=("sites:read",))
        with self.assertRaises(StateError):
            self.bind("operator-a", role.role_id, now=NOW - 1)
        binding = self.bind("operator-a", role.role_id)
        with self.assertRaises(StateError):
            self.store.revoke_binding(
                binding.binding_id,
                actor=AUDITOR,
                now=NOW - 1,
            )
        with self.assertRaises(StateError):
            self.store.set_role_enabled(
                role.role_id,
                False,
                actor=AUDITOR,
                now=NOW - 1,
            )


if __name__ == "__main__":
    unittest.main()  # noqa: F405
