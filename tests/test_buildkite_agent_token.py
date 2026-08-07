from __future__ import annotations

import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONFIGURE = ROOT / ".buildkite/agent/configure-agent.sh"
BOUND = ROOT / ".buildkite/agent/configure-bound-agent.sh"
DROPIN = ROOT / ".buildkite/agent/30-hostpanel-ephemeral.conf"
CONTRACT = ROOT / ".buildkite/scripts/run-pipeline-contract.sh"
SMOKE = ROOT / ".buildkite/operator/smoke-test-agent.sh"
DOCS = ROOT / "BUILDKITE.md"
CODEOWNERS = ROOT / ".github/CODEOWNERS"


class BuildkiteAgentTokenTests(unittest.TestCase):
    def test_registration_token_is_root_only_and_fd_backed(self) -> None:
        text = CONFIGURE.read_text(encoding="utf-8")
        self.assertIn(
            'install -o root -g root -m 0600 "$staged_token_file" "$token_destination"',
            text,
        )
        self.assertIn('stage_bytes("agent-token", token_data)', text)
        self.assertNotIn(
            'install -o root -g root -m 0600 "$token_file" "$token_destination"',
            text,
        )
        self.assertIn('token="fd://3"', text)
        self.assertIn("private input must be root-owned mode 0600", text)
        self.assertIn('rm -f -- "$token_file"', text)
        self.assertIn("consumed --token-file still exists", text)
        self.assertNotIn('token="file:///etc/buildkite-agent/agent-token"', text)

    def test_value_taking_options_guard_missing_values_before_shift(self) -> None:
        base = CONFIGURE.read_text(encoding="utf-8")
        for option in (
            "--queue",
            "--pipeline-slug",
            "--token-file",
            "--verification-jwks",
            "--signing-jwks",
            "--signing-key-id",
            "--checkout-key",
        ):
            snippet = f'{option})\n      (($# >= 2)) || fail "{option} requires a value"'
            self.assertIn(snippet, base)

        bound = BOUND.read_text(encoding="utf-8")
        for option in ("--pipeline-id", "--source-commit"):
            snippet = f'{option})\n      (($# >= 2)) || fail "{option} requires a value"'
            self.assertIn(snippet, bound)

    def test_systemd_opens_token_but_bound_wrapper_deletes_it(self) -> None:
        base = CONFIGURE.read_text(encoding="utf-8")
        self.assertIn("OpenFile=\n", base)
        self.assertIn(
            "OpenFile=/etc/buildkite-agent/agent-token:buildkite-agent-token:read-only",
            base,
        )
        self.assertIn(
            "ExecStartPost=+/usr/bin/rm -f /etc/buildkite-agent/agent-token",
            base,
        )
        self.assertIn("systemd-analyze verify buildkite-agent.service", base)

        dropin = DROPIN.read_text(encoding="utf-8")
        self.assertIn("Restart=no", dropin)
        self.assertIn("ExecStartPost=\n", dropin)
        self.assertNotIn("ReadWritePaths=/etc", dropin)
        self.assertIn("ExecStopPost=+/usr/bin/rm -f /etc/buildkite-agent/agent-token", dropin)

        bound = BOUND.read_text(encoding="utf-8")
        restart = bound.index("systemctl restart buildkite-agent.service")
        active = bound.index("Buildkite service is not active after registration", restart)
        delete = bound.index('rm -f -- "$token_destination"', active)
        survived = bound.index("registration token file survived bound-agent startup", delete)
        self.assertLess(restart, active)
        self.assertLess(active, delete)
        self.assertLess(delete, survived)

    def test_bound_wrapper_stops_any_preexisting_agent_before_staging(self) -> None:
        bound = BOUND.read_text(encoding="utf-8")
        active_check = 'if systemctl is-active --quiet buildkite-agent.service; then'
        stop = "systemctl stop buildkite-agent.service"
        fail_message = "a pre-existing Buildkite agent service remained active before provisioning"
        configure_call = '"$trusted_agent_script" "${base_args[@]}"'
        self.assertIn(active_check, bound)
        self.assertIn(stop, bound)
        self.assertIn(fail_message, bound)
        self.assertIn(configure_call, bound)
        self.assertLess(bound.index(stop), bound.index(configure_call))
        self.assertLess(bound.index(fail_message), bound.index(configure_call))

    def test_only_bound_wrapper_can_start_and_failure_cleans_credentials(self) -> None:
        base = CONFIGURE.read_text(encoding="utf-8")
        bound = BOUND.read_text(encoding="utf-8")
        configure_call = '"$trusted_agent_script" "${base_args[@]}"'
        self.assertNotIn("systemctl restart buildkite-agent.service", base)
        self.assertIn("bound wrapper must start the agent", base)
        self.assertIn(
            "--start is required so the one-time registration token cannot remain staged on disk",
            bound,
        )
        self.assertIn("cleanup_staged_credentials", bound)
        self.assertIn("checkout_key_destination", bound)
        self.assertIn(configure_call, bound)
        self.assertNotIn('"$script_dir/configure-agent.sh" "${base_args[@]}"', bound)
        self.assertIn("systemctl restart buildkite-agent.service", bound)
        self.assertIn("registration token file survived bound-agent startup", bound)
        self.assertIn("Buildkite service is not active after registration", bound)
        self.assertLess(
            bound.index("trap cleanup_staged_credentials EXIT"),
            bound.index(configure_call),
        )

    def test_smoke_and_docs_cover_one_time_registration(self) -> None:
        smoke = SMOKE.read_text(encoding="utf-8")
        self.assertIn('token="fd://3"', smoke)
        self.assertIn("registration token file remains after startup", smoke)
        self.assertIn("Buildkite service is not active", smoke)
        docs = DOCS.read_text(encoding="utf-8")
        for expected in (
            'token="fd://3"',
            "root-owned mode `0600`",
            "before enabling the pipeline",
            "without interrupting",
            "restarted or reconnected",
        ):
            self.assertIn(expected, docs)

    def test_contract_and_codeowners_cover_token_boundary(self) -> None:
        self.assertIn(
            "tests.test_buildkite_agent_token",
            CONTRACT.read_text(encoding="utf-8"),
        )
        self.assertIn(
            "/tests/test_buildkite_agent_token.py @1-vps",
            CODEOWNERS.read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
