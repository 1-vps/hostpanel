from __future__ import annotations

import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
BASE = ROOT / ".buildkite/operator/bootstrap-control-plane.py"
WRAPPER = ROOT / ".buildkite/operator/bootstrap-control-plane.sh"
RUNTIME = ROOT / ".buildkite/operator/control-plane-runtime.py"
CONTRACT = ROOT / ".buildkite/scripts/run-pipeline-contract.sh"
CODEOWNERS = ROOT / ".github/CODEOWNERS"


class BuildkiteControlPlaneEntrypointTests(unittest.TestCase):
    def test_direct_base_execution_is_rejected_before_argument_or_bk_processing(self) -> None:
        result = subprocess.run(
            [sys.executable, str(BASE), "--org", "example-org", "--apply", "--confirm-create"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("base library is not an executable entrypoint", result.stderr)
        self.assertIn("bootstrap-control-plane.sh", result.stderr)
        self.assertNotIn("bk CLI is unavailable", result.stderr)

    def test_shell_wrapper_executes_only_the_runtime_layer(self) -> None:
        text = WRAPPER.read_text(encoding="utf-8")
        self.assertIn('operator="$operator_dir/control-plane-runtime.py"', text)
        self.assertNotIn('operator="$operator_dir/bootstrap-control-plane.py"', text)
        self.assertIn('exec python3 "$operator" "$@"', text)

    def test_runtime_loads_base_as_library_not_entrypoint(self) -> None:
        text = RUNTIME.read_text(encoding="utf-8")
        self.assertIn('path = here / "bootstrap-control-plane.py"', text)
        self.assertIn("spec.loader.exec_module(module)", text)
        self.assertNotIn("base.main(", text)

    def test_pipeline_contract_and_codeowners_cover_entrypoint_test(self) -> None:
        self.assertIn(
            "tests.test_buildkite_control_plane_entrypoint",
            CONTRACT.read_text(encoding="utf-8"),
        )
        self.assertIn(
            "/tests/test_buildkite_control_plane_entrypoint.py @1-vps",
            CODEOWNERS.read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
