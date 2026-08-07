from __future__ import annotations

import pathlib
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONTROL_PLANE = ROOT / ".buildkite/operator/bootstrap-control-plane.sh"
CONTRACT = ROOT / ".buildkite/scripts/run-pipeline-contract.sh"
CODEOWNERS = ROOT / ".github/CODEOWNERS"


class BuildkiteControlPlaneTests(unittest.TestCase):
    def test_control_plane_tool_is_plan_only_by_default(self) -> None:
        result = subprocess.run(
            ["bash", str(CONTROL_PLANE), "--org", "example-org"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertIn("HostPanel Buildkite control-plane plan", result.stdout)
        self.assertIn("No agents are started by this tool.", result.stdout)
        self.assertIn("bk pipeline create 'HostPanel'", result.stdout)
        self.assertIn("--create-webhook", result.stdout)
        self.assertIn("STOP: do not connect any agent", result.stdout)

    def test_apply_requires_explicit_confirmation(self) -> None:
        result = subprocess.run(
            ["bash", str(CONTROL_PLANE), "--org", "example-org", "--apply"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--apply requires --confirm-create", result.stderr)

    def test_control_plane_tool_is_fail_closed_and_never_starts_agents(self) -> None:
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
            "bk pipeline create",
            "--create-webhook",
            "--cluster-uuid",
            "an existing HostPanel cluster was found",
            "an existing pipeline for the HostPanel repository was found",
            "No automatic cleanup was attempted",
            "MANDATORY STOP:",
        ):
            self.assertIn(expected, text)
        for forbidden in (
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
