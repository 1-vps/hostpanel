from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
VERIFIER = ROOT / ".buildkite/operator/verify-cluster-secrets.py"
RUNTIME = ROOT / ".buildkite/operator/control-plane-runtime.py"
CONTRACT = ROOT / ".buildkite/scripts/run-pipeline-contract.sh"
CODEOWNERS = ROOT / ".github/CODEOWNERS"

spec = importlib.util.spec_from_file_location("hostpanel_cluster_secret_verifier_test", VERIFIER)
assert spec and spec.loader
verifier = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = verifier
spec.loader.exec_module(verifier)

runtime_spec = importlib.util.spec_from_file_location("hostpanel_control_plane_runtime_secret_test", RUNTIME)
assert runtime_spec and runtime_spec.loader
runtime = importlib.util.module_from_spec(runtime_spec)
sys.modules[runtime_spec.name] = runtime
runtime_spec.loader.exec_module(runtime)
base = runtime.base

CLUSTER_ID = "01234567-89ab-cdef-0123-456789abcdef"


class BuildkiteClusterSecretsTests(unittest.TestCase):
    def test_endpoint_is_exact_cluster_scoped_and_bounded(self) -> None:
        self.assertEqual(
            verifier.endpoint("example-org", CLUSTER_ID),
            f"/organizations/example-org/clusters/{CLUSTER_ID}/secrets?per_page=100",
        )

    def test_scope_requires_read_secret_details(self) -> None:
        verifier.verify_scope_payload(json.dumps({"scopes": ["read_clusters", "read_secret_details"]}))
        with self.assertRaisesRegex(verifier.SecretVerificationError, "read_secret_details"):
            verifier.verify_scope_payload(json.dumps({"scopes": ["read_clusters"]}))

    def test_empty_inventory_passes(self) -> None:
        verifier.verify_empty_inventory("[]")

    def test_any_cluster_secret_is_a_hard_failure(self) -> None:
        payload = [{"id": "x", "key": "PRODUCTION_SECRET"}]
        with self.assertRaisesRegex(verifier.SecretVerificationError, "PRODUCTION_SECRET"):
            verifier.verify_empty_inventory(json.dumps(payload))

    def test_inventory_shape_and_pagination_are_fail_closed(self) -> None:
        for payload in ({"secrets": []}, ["bad"]):
            with self.subTest(payload=payload):
                with self.assertRaises(verifier.SecretVerificationError):
                    verifier.verify_empty_inventory(json.dumps(payload))
        with self.assertRaisesRegex(verifier.SecretVerificationError, "pagination"):
            verifier.verify_empty_inventory(json.dumps([{} for _ in range(100)]))

    def test_runtime_preflight_adds_secret_scope_to_base_scope_checks(self) -> None:
        calls: list[list[str]] = []

        def run_bk(args):
            calls.append(args)
            if args == ["api", "/access-token"]:
                return json.dumps({"scopes": ["read_secret_details"]})
            raise AssertionError(args)

        with mock.patch.object(base, "preflight") as base_preflight, mock.patch.object(
            base, "run_bk", side_effect=run_bk
        ):
            runtime.preflight("example-org")
        base_preflight.assert_called_once_with("example-org")
        self.assertEqual(calls, [["api", "/access-token"]])

    def test_runtime_empty_secret_check_uses_exact_endpoint(self) -> None:
        expected = f"/organizations/example-org/clusters/{CLUSTER_ID}/secrets?per_page=100"
        with mock.patch.object(base, "run_bk", return_value="[]") as run:
            runtime.assert_no_cluster_secrets("example-org", CLUSTER_ID)
        run.assert_called_once_with(["api", expected])

    def test_runtime_rejects_nonempty_secret_inventory(self) -> None:
        payload = json.dumps([{"key": "UNEXPECTED_SECRET"}])
        with mock.patch.object(base, "run_bk", return_value=payload):
            with self.assertRaisesRegex(base.OperatorError, "UNEXPECTED_SECRET"):
                runtime.assert_no_cluster_secrets("example-org", CLUSTER_ID)

    def test_pipeline_contract_and_codeowners_cover_secret_tests(self) -> None:
        self.assertIn(
            "tests.test_buildkite_cluster_secrets",
            CONTRACT.read_text(encoding="utf-8"),
        )
        self.assertIn(
            "/tests/test_buildkite_cluster_secrets.py @1-vps",
            CODEOWNERS.read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
