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
        self.assertIn("trigger_mode=none", result.stdout)
        self.assertIn("branch/PR/tag/comment triggers disabled", result.stdout)
        self.assertIn("webhook presence never activates", result.stdout)
        self.assertIn("MANDATORY STOP:", result.stdout)

    def test_activation_plan_preserves_quarantine_until_verified(self) -> None:
        result = self.run_tool(
            "--org", "example-org", "--enable-webhook", "hostpanel"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("trigger activation plan", result.stdout)
        self.assertIn("/access-token", result.stdout)
        self.assertIn("trigger_mode=none", result.stdout)
        self.assertIn("If a valid Buildkite webhook already exists", result.stdout)
        self.assertIn("PATCH provider_settings", result.stdout)
        self.assertIn("trigger_mode=code", result.stdout)

    def test_create_apply_requires_explicit_confirmation(self) -> None:
        result = self.run_tool("--org", "example-org", "--apply")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--apply requires --confirm-create", result.stderr)

    def test_activation_apply_requires_signed_bootstrap_confirmation(self) -> None:
        result = self.run_tool(
            "--org", "example-org", "--enable-webhook", "hostpanel", "--apply"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--confirm-static-bootstrap-signed", result.stderr)

    def test_activation_apply_requires_public_deploy_key_confirmation(self) -> None:
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

    def test_scope_preflight_is_mandatory_before_resource_writes(self) -> None:
        text = CONTROL_PLANE.read_text(encoding="utf-8")
        scope_index = text.index('access_token_metadata="$(bk api /access-token)"')
        cluster_write = text.index("bk cluster create")
        pipeline_write = text.index("bk api --method POST /pipelines --data")
        self.assertLess(scope_index, cluster_write)
        self.assertLess(scope_index, pipeline_write)
        for scope in (
            "read_clusters",
            "write_clusters",
            "read_pipelines",
            "write_pipelines",
            "read_builds",
            "read_agents",
        ):
            self.assertIn(f'"{scope}"', text)
        self.assertIn("missing required scope(s)", text)

    def test_pipeline_creation_uses_fail_closed_quarantine_policy(self) -> None:
        text = CONTROL_PLANE.read_text(encoding="utf-8")
        self.assertIn('"trigger_mode": "none"', text)
        for setting in (
            '"build_branches": False',
            '"build_pull_requests": False',
            '"build_tags": False',
            '"publish_commit_status": False',
            '"build_issue_comment_created": False',
        ):
            self.assertIn(setting, text)
        self.assertIn('"trigger_mode": "code"', text)
        self.assertIn('"build_pull_requests": True', text)
        self.assertIn('"publish_commit_status": True', text)

    def test_webhook_is_idempotent_and_activation_is_explicit_patch(self) -> None:
        text = CONTROL_PLANE.read_text(encoding="utf-8")
        self.assertIn('if [[ -z "$webhook_url" ]]; then', text)
        self.assertIn(
            'bk api --method POST "/pipelines/$enable_webhook_slug/webhook"',
            text,
        )
        self.assertIn("--method PATCH", text)
        self.assertIn('"/pipelines/$enable_webhook_slug"', text)
        self.assertIn('"provider_settings"', text)
        self.assertNotIn("unexpectedly exposes a webhook before activation", text)

    def test_control_plane_tool_retains_hard_security_boundaries(self) -> None:
        text = CONTROL_PLANE.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("#!/usr/bin/env bash\nset -euo pipefail\n"))
        for expected in (
            'bk auth switch "$org"',
            "bk auth status -o json",
            "bk cluster list -o json",
            'bk pipeline list --repository "$repository" -o json',
            "default_queue_id",
            "hostpanel-upload",
            "hostpanel-ci",
            "hostpanel-qemu",
            "static bootstrap is not signed",
            "static bootstrap signature algorithm is not EdDSA",
            "static bootstrap JWS key ID mismatch",
            "pipeline already has build history",
            "agent inventory reached the query limit",
            "an agent is already connected to the HostPanel cluster",
            "No automatic cleanup was attempted",
        ):
            self.assertIn(expected, text)

        for forbidden in (
            "--create-webhook",
            "/github-webhooks",
            "bk auth token",
            "buildkite-agent start",
            "systemctl start",
            "systemctl restart",
            "agent-token",
        ):
            self.assertNotIn(forbidden, text)

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
