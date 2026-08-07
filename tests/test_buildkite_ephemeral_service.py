from __future__ import annotations

import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
DROPIN = ROOT / ".buildkite/agent/30-hostpanel-ephemeral.conf"
BOUND = ROOT / ".buildkite/agent/configure-bound-agent.sh"
CONTRACT = ROOT / ".buildkite/scripts/run-pipeline-contract.sh"
CODEOWNERS = ROOT / ".github/CODEOWNERS"


class BuildkiteEphemeralServiceTests(unittest.TestCase):
    def test_systemd_dropin_disables_restart_and_clears_service_side_start_cleanup(self) -> None:
        self.assertEqual(
            DROPIN.read_text(encoding="utf-8"),
            "[Service]\n"
            "Restart=no\n"
            "ExecStartPost=\n"
            "ExecStopPost=+/usr/bin/rm -f /etc/buildkite-agent/agent-token "
            "/run/hostpanel-buildkite/checkout-deploy-key\n",
        )

    def test_token_cleanup_does_not_require_a_writable_etc_mount(self) -> None:
        text = DROPIN.read_text(encoding="utf-8")
        self.assertNotIn("ReadWritePaths=/etc", text)
        self.assertIn("ExecStartPost=\n", text)

    def test_bound_provisioner_installs_root_owned_dropin(self) -> None:
        text = BOUND.read_text(encoding="utf-8")
        self.assertIn('"$trusted_agent_dir/30-hostpanel-ephemeral.conf"', text)
        self.assertNotIn('"$script_dir/30-hostpanel-ephemeral.conf"', text)
        self.assertIn(
            "/etc/systemd/system/buildkite-agent.service.d/30-hostpanel-ephemeral.conf",
            text,
        )
        self.assertIn("-o root -g root -m 0644", text)
        trusted_source = text.index('"$trusted_agent_dir/30-hostpanel-ephemeral.conf"')
        daemon_reload = text.index("systemctl daemon-reload")
        self.assertLess(trusted_source, daemon_reload)
        self.assertIn("systemd-analyze verify buildkite-agent.service", text)

    def test_bound_start_is_atomic_for_all_staged_credentials(self) -> None:
        text = BOUND.read_text(encoding="utf-8")
        self.assertIn(
            "--start is required so the one-time registration token cannot remain staged on disk",
            text,
        )
        configure_call = '"$trusted_agent_script" "${base_args[@]}"'
        trap_index = text.index("trap cleanup_staged_credentials EXIT")
        base_index = text.index(configure_call)
        restart_index = text.index("systemctl restart buildkite-agent.service")
        active_index = text.index(
            'systemctl is-active --quiet buildkite-agent.service || fail "Buildkite service is not active after registration"',
            restart_index,
        )
        delete_index = text.index('rm -f -- "$token_destination"', active_index)
        survived_index = text.index("registration token file survived bound-agent startup", delete_index)
        clear_index = text.index("trap - EXIT HUP INT TERM", survived_index)
        self.assertNotIn('"$script_dir/configure-agent.sh" "${base_args[@]}"', text)
        self.assertLess(trap_index, base_index)
        self.assertLess(base_index, restart_index)
        self.assertLess(restart_index, active_index)
        self.assertLess(active_index, delete_index)
        self.assertLess(delete_index, survived_index)
        self.assertLess(survived_index, clear_index)
        self.assertIn("outside that namespace after registration", text)
        self.assertIn("checkout_key_destination", text)

    def test_pipeline_contract_runs_ephemeral_tests(self) -> None:
        self.assertIn(
            "tests.test_buildkite_ephemeral_service",
            CONTRACT.read_text(encoding="utf-8"),
        )

    def test_codeowners_covers_all_buildkite_tests(self) -> None:
        self.assertIn(
            "/tests/test_buildkite_ephemeral_service.py @1-vps",
            CODEOWNERS.read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
