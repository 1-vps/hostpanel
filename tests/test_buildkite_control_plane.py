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

    def test_creation_plan_is_read_only_and_has_no_webhook(self) -> None:
        result = self.run_tool("--org", "example-org")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("control-plane creation plan", result.stdout)
        self.assertIn("No agents are started by this tool.", result.stdout)
        self.assertIn("No GitHub webhook is created in this phase.", result.stdout)
        self.assertIn("no-checkout bootstrap", result.stdout)
        self.assertIn("use --enable-webhook", result.stdout)
        self.assertNotIn("--create-webhook", result.stdout)

    def test_webhook_activation_is_a_separate_plan(self) -> None:
        result = self.run_tool(
            "--org", "example-org", "--enable-webhook", "hostpanel"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("webhook activation plan", result.stdout)
        self.assertIn("statically signed", result.stdout)
        self.assertIn("public half of the read-only checkout deploy key", result.stdout)
        self.assertIn("No build has ever been created", result.stdout)
        self.assertIn("No agent is connected", result.stdout)
        self.assertIn("zero-build", result.stdout)

    def test_create_apply_requires_explicit_confirmation(self) -> None:
        result = self.run_tool("--org", "example-org", "--apply")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--apply requires --confirm-create", result.stderr)

    def test_webhook_apply_requires_signed_bootstrap_confirmation(self) -> None:
        result = self.run_tool(
            "--org", "example-org", "--enable-webhook", "hostpanel", "--apply"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--confirm-static-bootstrap-signed", result.stderr)

    def test_webhook_apply_requires_public_deploy_key_confirmation(self) -> None:
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

    def test_control_plane_tool_is_fail_closed(self) -> None:
        text = CONTROL_PLANE.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("#!/usr/bin/env bash\nset -euo pipefail\n"))
        for expected in (
            'bk auth switch "$org"',
            "bk auth status -o json",
            "bk cluster list -o json",
            'bk pipeline list --repository "$repository" -o json',
            "bk cluster create",
            "bk queue create",
            "hostpanel-upload",
            "hostpanel-ci",
            "hostpanel-qemu",
            "bk api --method POST /pipelines --data",
            '"/pipelines/$enable_webhook_slug/webhook"',
            '"/pipelines/$enable_webhook_slug/github-webhooks"',
            "python3 -c 'import yaml'",
            "static bootstrap is not signed",
            "signature algorithm is not EdDSA",
            "static bootstrap JWS key ID mismatch",
            "pipeline already has build history",
            "an agent is already connected to the HostPanel cluster",
            "No automatic cleanup was attempted",
            "MANDATORY STOP:",
        ):
            self.assertIn(expected, text)
        for forbidden in (
            "--create-webhook",
            "bk auth token",
            "--token",
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
