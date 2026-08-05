from __future__ import annotations

from tests.rbac_test_support import *  # noqa: F403


class HostpanelApiRbacPolicyTests(RbacTestCase):  # noqa: F405
    def test_default_deny_and_exact_allow(self) -> None:
        self.principal("operator-a")
        denied = self.store.simulate(
            principal_id="operator-a",
            capability="sites:read",
            tenant_id="tenant-a",
            now=NOW,
        )
        self.assertFalse(denied.allowed)
        self.assertEqual(denied.reason, "default_deny")

        reader = self.role("Site reader", allows=("sites:read",))
        binding = self.bind("operator-a", reader.role_id)
        allowed = self.store.authorize(
            principal_id="operator-a",
            capability="sites:read",
            tenant_id="tenant-a",
            now=NOW,
        )
        self.assertTrue(allowed.allowed)
        self.assertEqual(allowed.allow_bindings, (binding.binding_id,))
        with self.assertRaises(AuthorizationError):
            self.store.authorize(
                principal_id="operator-a",
                capability="sites:write",
                tenant_id="tenant-a",
                now=NOW,
            )

    def test_explicit_deny_overrides_allow_across_roles(self) -> None:
        self.principal("operator-a")
        allow = self.role("Allow", allows=("sites:write",))
        deny = self.role("Deny", denies=("sites:write",))
        allow_binding = self.bind("operator-a", allow.role_id)
        deny_binding = self.bind("operator-a", deny.role_id)
        decision = self.store.simulate(
            principal_id="operator-a",
            capability="sites:write",
            tenant_id="tenant-a",
            now=NOW,
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "explicit_deny")
        self.assertEqual(decision.allow_bindings, (allow_binding.binding_id,))
        self.assertEqual(decision.deny_bindings, (deny_binding.binding_id,))

    def test_tenant_and_resource_boundaries_are_both_required(self) -> None:
        self.principal("reseller-a", "reseller")
        role = self.role("Site manager", allows=("sites:write",))
        binding = self.bind(
            "reseller-a",
            role.role_id,
            tenant_ids=("tenant-a",),
            resources={"site": ("example.com",)},
            allow_all_resources=False,
        )
        allowed = self.store.simulate(
            principal_id="reseller-a",
            capability="sites:write",
            tenant_id="tenant-a",
            resource_type="site",
            resource_id="example.com",
            now=NOW,
        )
        self.assertTrue(allowed.allowed)
        self.assertEqual(allowed.allow_bindings, (binding.binding_id,))
        for request in (
            {"tenant_id": "tenant-b", "resource_type": "site", "resource_id": "example.com"},
            {"tenant_id": "tenant-a", "resource_type": "site", "resource_id": "other.example"},
            {"tenant_id": "tenant-a"},
        ):
            with self.subTest(request=request):
                decision = self.store.simulate(
                    principal_id="reseller-a",
                    capability="sites:write",
                    now=NOW,
                    **request,
                )
                self.assertFalse(decision.allowed)
                self.assertEqual(decision.reason, "default_deny")

    def test_disabled_expired_and_revoked_policy_objects_fail_closed(self) -> None:
        self.principal("operator-a")
        role = self.role("Reader", allows=("sites:read",))
        binding = self.bind("operator-a", role.role_id, expires_at=NOW + 10)
        self.assertTrue(
            self.store.simulate(
                principal_id="operator-a",
                capability="sites:read",
                tenant_id="tenant-a",
                now=NOW + 1,
            ).allowed
        )
        self.assertFalse(
            self.store.simulate(
                principal_id="operator-a",
                capability="sites:read",
                tenant_id="tenant-a",
                now=NOW + 10,
            ).allowed
        )
        self.store.revoke_binding(binding.binding_id, actor=AUDITOR, now=NOW + 2)
        self.assertFalse(
            self.store.simulate(
                principal_id="operator-a",
                capability="sites:read",
                tenant_id="tenant-a",
                now=NOW + 3,
            ).allowed
        )
        replacement = self.bind("operator-a", role.role_id, now=NOW + 3)
        self.store.set_role_enabled(role.role_id, False, actor=AUDITOR, now=NOW + 4)
        self.assertFalse(
            self.store.simulate(
                principal_id="operator-a",
                capability="sites:read",
                tenant_id="tenant-a",
                now=NOW + 5,
            ).allowed
        )
        self.store.set_role_enabled(role.role_id, True, actor=AUDITOR, now=NOW + 6)
        self.store.set_principal_enabled("operator-a", False, actor=AUDITOR, now=NOW + 7)
        decision = self.store.simulate(
            principal_id="operator-a",
            capability="sites:read",
            tenant_id="tenant-a",
            now=NOW + 8,
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "principal_inactive")
        self.assertEqual(replacement.revoked_at, None)

    def test_role_capability_replacement_takes_effect_immediately(self) -> None:
        self.principal("operator-a")
        role = self.role("Mutable", allows=("sites:read",))
        self.bind("operator-a", role.role_id)
        self.assertTrue(
            self.store.simulate(
                principal_id="operator-a",
                capability="sites:read",
                tenant_id="tenant-a",
                now=NOW,
            ).allowed
        )
        updated = self.store.replace_role_capabilities(
            role.role_id,
            allows=("sites:write",),
            actor=AUDITOR,
            now=NOW + 1,
        )
        self.assertEqual(updated.allows, ("sites:write",))
        self.assertFalse(
            self.store.simulate(
                principal_id="operator-a",
                capability="sites:read",
                tenant_id="tenant-a",
                now=NOW + 1,
            ).allowed
        )
        self.assertTrue(
            self.store.simulate(
                principal_id="operator-a",
                capability="sites:write",
                tenant_id="tenant-a",
                now=NOW + 1,
            ).allowed
        )

    def test_policy_decision_uses_one_database_snapshot(self) -> None:
        self.principal("operator-a")
        role = self.role("Reader", allows=("sites:read",))
        self.bind("operator-a", role.role_id)
        statements: list[str] = []
        self.connection.set_trace_callback(statements.append)
        decision = self.store.simulate(
            principal_id="operator-a",
            capability="sites:read",
            tenant_id="tenant-a",
            now=NOW,
        )
        self.connection.set_trace_callback(None)
        self.assertTrue(decision.allowed)
        self.assertTrue(statements[0].startswith("SAVEPOINT hp_api_rbac_"), statements)
        self.assertTrue(statements[-1].startswith("RELEASE hp_api_rbac_"), statements)
        self.assertFalse(self.connection.in_transaction)


if __name__ == "__main__":
    unittest.main()  # noqa: F405
