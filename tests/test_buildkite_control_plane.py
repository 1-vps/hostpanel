from __future__ import annotations

import pathlib
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONTROL_PLANE = ROOT / ".buildkite/operator/bootstrap-control-plane.sh"
CONTRACT = ROOT / ".buildkite/scripts/run-pipeline-contract.sh"
CODEOWNERS = ROOT / ".github/CODEOWNERS"


class BuildkiteControlPlaneTests(unittest.TestCase):
    def run_tool(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(CONTROL_PLANE), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def test_creation_plan_is_read_only_and_quarantined(self) -> None:
        result = self.run_tool("--org", "example-org")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("control-plane creation plan", result.stdout)
        self.assertIn("No agents are started by this tool.", result.stdout)
        self.assertIn("trigger_mode=none quarantine", result.stdout)
        self.assertIn("cluster_queue_id", result.stdout)
        self.assertIn("MANDATORY STOP:", result.stdout)

    def test_activation_is_a_separate_plan(self) -> None:
        result = self.run_tool(
            "--org", "example-org", "--enable-webhook", "hostpanel"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("activation plan", result.stdout)
        self.assertIn("statically signed", result.stdout)
        self.assertIn("zero connected/stopping agents", result.stdout)
        self.assertIn("PATCHes only provider_settings", result.stdout)

    def test_create_apply_requires_explicit_confirmation(self) -> None:
        result = self.run_tool("--org", "example-org", "--apply")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--apply requires --confirm-create", result.stderr)

    def test_activation_requires_signed_bootstrap_confirmation(self) -> None:
        result = self.run_tool(
            "--org", "example-org", "--enable-webhook", "hostpanel", "--apply"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--confirm-static-bootstrap-signed", result.stderr)

    def test_activation_requires_public_deploy_key_confirmation(self) -> None:
        result = self.run_tool(
            "--org",
            "example-org",
            "--enable-webhook",
            "hostpanel",
            "--apply",
            "--confirm-static-bootstrap-signed",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--confirm-public-deploy-key-added", result.stderr)

    def test_required_scope_preflight_precedes_resource_writes(self) -> None:
        text = CONTROL_PLANE.read_text(encoding="utf-8")
        preflight = text.index('token_json="$(bk api /access-token)"')
        self.assertLess(preflight, text.index("bk cluster create"))
        self.assertLess(preflight, text.index("bk api --method POST /pipelines"))
        for scope in (
            "read_clusters",
            "write_clusters",
            "read_pipelines",
            "write_pipelines",
            "read_builds",
            "read_agents",
        ):
            self.assertIn(f'"{scope}"', text)

    def test_connected_agent_proof_is_queue_uuid_scoped(self) -> None:
        text = CONTROL_PLANE.read_text(encoding="utf-8")
        self.assertIn(
            'bk api "/organizations/$org/agents?cluster_queue_id=$queue_uuid"',
            text,
        )
        self.assertIn("assert_queue_has_no_connected_agents", text)
        self.assertIn("assert_no_target_agents", text)
        self.assertIn("hostpanel-upload", text)
        self.assertIn("hostpanel-ci", text)
        self.assertIn("hostpanel-qemu", text)
        self.assertNotIn("bk agent list", text)
        self.assertNotIn("--limit 1000", text)

    def test_quarantine_to_active_transition_is_explicit(self) -> None:
        text = CONTROL_PLANE.read_text(encoding="utf-8")
        self.assertIn('"trigger_mode": "none"', text)
        self.assertIn('"trigger_mode": "code"', text)
        self.assertIn(
            'bk api --method PATCH "/pipelines/$enable_webhook_slug" --data "$active_patch"',
            text,
        )
        self.assertIn("verify_pipeline quarantine", text)
        self.assertIn("verify_pipeline active", text)
        self.assertLess(
            text.index('"trigger_mode": "none"'),
            text.index('"trigger_mode": "code"'),
        )

    def test_webhook_handling_is_idempotent_and_stays_quarantined(self) -> None:
        text = CONTROL_PLANE.read_text(encoding="utf-8")
        self.assertIn('if [[ -z "$webhook_url" ]]; then', text)
        self.assertIn(
            'bk api --method POST "/pipelines/$enable_webhook_slug/webhook"',
            text,
        )
        self.assertIn(
            'verify_pipeline quarantine "$enable_webhook_slug" "$pipeline_json" true true',
            text,
        )
        self.assertNotIn("/github-webhooks", text)

    def test_control_plane_tool_keeps_security_forbidden_actions_out(self) -> None:
        text = CONTROL_PLANE.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("#!/usr/bin/env bash\nset -euo pipefail\n"))
        for forbidden in (
            "bk auth token",
            "--token",
            "buildkite-agent start",
            "systemctl start",
            "systemctl restart",
            "agent-token",
            "--create-webhook",
        ):
            self.assertNotIn(forbidden, text)
        self.assertIn("No automatic cleanup was attempted", text)
        self.assertIn("static bootstrap JWS key ID mismatch", text)
        self.assertIn("build_pull_request_merge_commits", text)

    def test_embedded_python_blocks_compile(self) -> None:
        text = CONTROL_PLANE.read_text(encoding="utf-8")
        blocks = text.split("<<'PY'\n")[1:]
        self.assertGreaterEqual(len(blocks), 10)
        for index, tail in enumerate(blocks, 1):
            code = tail.split("\nPY\n", 1)[0]
            compile(code, f"<control-plane-heredoc-{index}>", "exec")

    def test_pipeline_contract_and_codeowners_cover_control_plane_tool(self) -> None:
        self.assertIn(
            "tests.test_buildkite_control_plane",
            CONTRACT.read_text(encoding="utf-8"),
        )
        self.assertIn(
            "/tests/test_buildkite_control_plane.py @1-vps",
            CODEOWNERS.read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
