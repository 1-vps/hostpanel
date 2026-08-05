from __future__ import annotations

from tests.rbac_test_support import *  # noqa: F403


class HostpanelApiRbacImpersonationTests(RbacTestCase):  # noqa: F405
    def prepare_principals(self, *, actor_site: bool = True, target_site: bool = True):
        self.principal("support-a", "operator")
        self.principal("customer-a", "customer")
        support = self.role("Support impersonation", allows=("support:impersonate",))
        support_binding = self.bind(
            "support-a",
            support.role_id,
            resources={"principal": ("customer-a",)},
            allow_all_resources=False,
        )
        if actor_site:
            actor_role = self.role("Actor site access", allows=("sites:write",))
            self.bind("support-a", actor_role.role_id)
        if target_site:
            target_role = self.role("Target site access", allows=("sites:write",))
            self.bind("customer-a", target_role.role_id)
        return support_binding

    def start(self, **overrides):
        values = {
            "actor_principal_id": "support-a",
            "target_principal_id": "customer-a",
            "tenant_id": "tenant-a",
            "reason": "Investigate customer deployment failure",
            "expires_at": NOW + 600,
            "actor": SUPPORT_ACTOR,
            "now": NOW,
        }
        values.update(overrides)
        return self.store.start_impersonation(**values)

    def test_impersonation_requires_support_permission_for_exact_target(self) -> None:
        self.principal("support-a", "operator")
        self.principal("customer-a", "customer")
        support = self.role("Support", allows=("support:impersonate",))
        self.bind(
            "support-a",
            support.role_id,
            resources={"principal": ("other-customer",)},
            allow_all_resources=False,
        )
        with self.assertRaises(AuthorizationError):
            self.start()
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM hp_api_impersonation_sessions"
            ).fetchone()[0],
            0,
        )

    def test_impersonated_action_is_intersection_of_actor_and_target(self) -> None:
        self.prepare_principals()
        session = self.start()
        decision = self.store.authorize_impersonation(
            session.session_id,
            capability="sites:write",
            tenant_id="tenant-a",
            actor=SUPPORT_ACTOR,
            now=NOW + 1,
        )
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.reason, "intersection_allow")
        self.assertTrue(decision.actor_decision.allowed)
        self.assertTrue(decision.target_decision.allowed)

    def test_each_enforced_impersonated_action_is_append_only_audited(self) -> None:
        self.prepare_principals()
        session = self.start()
        self.store.authorize_impersonation(
            session.session_id,
            capability="sites:write",
            tenant_id="tenant-a",
            actor=SUPPORT_ACTOR,
            now=NOW + 1,
        )
        events = self.store.list_audit_events(limit=100)
        action = events[-1]
        self.assertEqual(action.action, "impersonation.action_allowed")
        self.assertEqual(action.actor_id, "support-a")
        self.assertEqual(action.metadata["target_principal_id"], "customer-a")

        self.store.set_principal_enabled(
            "customer-a", False, actor=AUDITOR, now=NOW + 2
        )
        with self.assertRaises(AuthorizationError):
            self.store.authorize_impersonation(
                session.session_id,
                capability="sites:write",
                tenant_id="tenant-a",
                actor=SUPPORT_ACTOR,
                now=NOW + 3,
            )
        denied = self.store.list_audit_events(limit=100)[-1]
        self.assertEqual(denied.action, "impersonation.action_denied")
        self.assertEqual(denied.metadata["decision_reason"], "target_capability_denied")

    def test_actor_or_target_denial_blocks_impersonated_action(self) -> None:
        self.prepare_principals(actor_site=False, target_site=True)
        session = self.start()
        decision = self.store.simulate_impersonation(
            session.session_id,
            capability="sites:write",
            tenant_id="tenant-a",
            now=NOW + 1,
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "actor_capability_denied")

        self.connection.close()
        self.connection = sqlite3.connect(":memory:")
        self.store = ApiRbacStore(self.connection)
        self.store.migrate()
        self.prepare_principals(actor_site=True, target_site=False)
        session = self.start()
        decision = self.store.simulate_impersonation(
            session.session_id,
            capability="sites:write",
            tenant_id="tenant-a",
            now=NOW + 1,
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "target_capability_denied")

    def test_support_permission_is_rechecked_for_every_action(self) -> None:
        support_binding = self.prepare_principals()
        session = self.start()
        self.store.revoke_binding(
            support_binding.binding_id,
            actor=AUDITOR,
            now=NOW + 1,
        )
        decision = self.store.simulate_impersonation(
            session.session_id,
            capability="sites:write",
            tenant_id="tenant-a",
            now=NOW + 2,
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "support_permission_revoked")

    def test_session_is_tenant_bound_expires_and_can_be_ended(self) -> None:
        self.prepare_principals()
        session = self.start(expires_at=NOW + 10)
        with self.assertRaises(StateError):
            self.store.simulate_impersonation(
                session.session_id,
                capability="sites:write",
                tenant_id="tenant-b",
                now=NOW + 1,
            )
        with self.assertRaises(StateError):
            self.store.simulate_impersonation(
                session.session_id,
                capability="sites:write",
                tenant_id="tenant-a",
                now=NOW + 10,
            )
        ended = self.store.end_impersonation(
            session.session_id,
            reason="Support investigation completed",
            actor=AUDITOR,
            now=NOW + 2,
        )
        self.assertEqual(ended.ended_at, NOW + 2)
        with self.assertRaises(StateError):
            self.store.end_impersonation(
                session.session_id,
                reason="Duplicate end",
                actor=AUDITOR,
                now=NOW + 3,
            )

    def test_expired_session_is_closed_before_replacement(self) -> None:
        self.prepare_principals()
        first = self.start(expires_at=NOW + 5)
        second = self.start(now=NOW + 6, expires_at=NOW + 100)
        self.assertNotEqual(first.session_id, second.session_id)
        old = self.store._session(first.session_id)
        self.assertEqual(old.ended_at, NOW + 6)
        self.assertEqual(old.end_reason, "expired")

    def test_only_one_active_session_per_actor_across_connections(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "rbac.db")
            setup = sqlite3.connect(database)
            setup_store = ApiRbacStore(setup)
            setup_store.migrate()
            self.connection.close()
            self.connection = setup
            self.store = setup_store
            self.prepare_principals()
            setup.commit()
            setup.close()
            barrier = threading.Barrier(2)
            sessions = []
            errors = []

            def run(suffix: str) -> None:
                connection = sqlite3.connect(database, timeout=5)
                store = ApiRbacStore(connection)
                try:
                    barrier.wait(timeout=5)
                    sessions.append(
                        store.start_impersonation(
                            actor_principal_id="support-a",
                            target_principal_id="customer-a",
                            tenant_id="tenant-a",
                            reason=f"Concurrent support investigation {suffix}",
                            expires_at=NOW + 600,
                            actor=SUPPORT_ACTOR,
                            now=NOW,
                        )
                    )
                except BaseException as exc:
                    errors.append(exc)
                finally:
                    connection.close()

            threads = [
                threading.Thread(target=run, args=("one",)),
                threading.Thread(target=run, args=("two",)),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)
            self.assertFalse(any(thread.is_alive() for thread in threads))
            self.assertEqual(len(sessions), 1)
            self.assertEqual(len(errors), 1)
            self.assertIsInstance(errors[0], ConflictError)
            self.connection = sqlite3.connect(":memory:")
            self.store = ApiRbacStore(self.connection)
            self.store.migrate()

    def test_impersonation_audit_actor_must_match_authenticated_actor(self) -> None:
        self.prepare_principals()
        with self.assertRaisesRegex(
            ValidationError, "audit actor must match actor principal"
        ):
            self.start(actor=AUDITOR)
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM hp_api_impersonation_sessions"
            ).fetchone()[0],
            0,
        )

    def test_self_impersonation_duration_reason_and_target_state_fail_closed(self) -> None:
        self.prepare_principals()
        for overrides in (
            {"target_principal_id": "support-a"},
            {"expires_at": NOW + 3601},
            {"reason": "short"},
        ):
            with self.subTest(overrides=overrides), self.assertRaises(ValidationError):
                self.start(**overrides)
        self.store.set_principal_enabled(
            "customer-a",
            False,
            actor=AUDITOR,
            now=NOW + 1,
        )
        with self.assertRaises(StateError):
            self.start(now=NOW + 2, expires_at=NOW + 100)


if __name__ == "__main__":
    unittest.main()  # noqa: F405
