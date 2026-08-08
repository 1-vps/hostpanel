from __future__ import annotations

import importlib.util
import pathlib
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOKEN_TOOL = ROOT / ".buildkite/operator/manage-agent-token.py"
CONTRACT = ROOT / ".buildkite/scripts/run-pipeline-contract.sh"
CODEOWNERS = ROOT / ".github/CODEOWNERS"

spec = importlib.util.spec_from_file_location("hostpanel_agent_token_ambiguous", TOKEN_TOOL)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

CLUSTER_ID = "01234567-89ab-cdef-0123-456789abcdef"
TOKEN_ID = "11111111-2222-3333-4444-555555555555"
DESCRIPTION = "hostpanel-worker:ci-01"


def args():
    return SimpleNamespace(
        org="example-org",
        cluster_id=CLUSTER_ID,
        worker_id="ci-01",
        allowed_ip="1.1.1.1",
        output_file="/run/hostpanel-buildkite-tokens/ci-01.token",
        ttl_minutes=15,
        apply=True,
        confirm_create=True,
    )


class BuildkiteAgentTokenAmbiguousCreateTests(unittest.TestCase):
    def common_patches(self):
        return (
            mock.patch.object(module, "require_apply_root"),
            mock.patch.object(module, "ensure_token_root"),
            mock.patch.object(module, "secure_parent"),
            mock.patch.object(module, "preflight"),
            mock.patch.object(module, "verify_hostpanel_cluster"),
            mock.patch.object(module, "verify_no_cluster_secrets"),
        )

    def test_ambiguous_create_recovers_one_matching_token_and_revokes_it(self) -> None:
        patches = self.common_patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], mock.patch.object(
            module,
            "worker_token_matches",
            side_effect=[[], [{"id": TOKEN_ID, "description": DESCRIPTION}]],
        ), mock.patch.object(
            module, "run_bk", side_effect=module.OperatorError("synthetic ambiguous POST")
        ), mock.patch.object(module, "revoke_created_token", return_value=True) as revoke:
            with self.assertRaisesRegex(module.OperatorError, "recovered and revoked"):
                module.create_token(args())
        revoke.assert_called_once_with(CLUSTER_ID, TOKEN_ID)

    def test_ambiguous_create_with_no_unique_match_requires_manual_inventory_cleanup(self) -> None:
        patches = self.common_patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], mock.patch.object(
            module, "worker_token_matches", side_effect=[[], []]
        ), mock.patch.object(
            module, "run_bk", side_effect=module.OperatorError("synthetic ambiguous POST")
        ):
            with self.assertRaisesRegex(module.OperatorError, "revoke every matching token"):
                module.create_token(args())

    def test_ambiguous_create_with_unreadable_inventory_stops_retry(self) -> None:
        patches = self.common_patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], mock.patch.object(
            module,
            "worker_token_matches",
            side_effect=[[], module.OperatorError("synthetic inventory failure")],
        ), mock.patch.object(
            module, "run_bk", side_effect=module.OperatorError("synthetic ambiguous POST")
        ):
            with self.assertRaisesRegex(module.OperatorError, "inventory could not be verified"):
                module.create_token(args())

    def test_ambiguous_create_recovered_token_must_be_proven_revoked(self) -> None:
        patches = self.common_patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], mock.patch.object(
            module,
            "worker_token_matches",
            side_effect=[[], [{"id": TOKEN_ID, "description": DESCRIPTION}]],
        ), mock.patch.object(
            module, "run_bk", side_effect=module.OperatorError("synthetic ambiguous POST")
        ), mock.patch.object(module, "revoke_created_token", return_value=False):
            with self.assertRaisesRegex(module.OperatorError, TOKEN_ID):
                module.create_token(args())

    def test_ambiguous_create_never_continues_to_secret_write(self) -> None:
        patches = self.common_patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], mock.patch.object(
            module, "worker_token_matches", side_effect=[[], []]
        ), mock.patch.object(
            module, "run_bk", side_effect=module.OperatorError("synthetic ambiguous POST")
        ), mock.patch.object(module, "write_secret_exclusive") as write_secret:
            with self.assertRaises(module.OperatorError):
                module.create_token(args())
        write_secret.assert_not_called()

    def test_pipeline_contract_and_codeowners_cover_ambiguous_create_tests(self) -> None:
        self.assertIn(
            "tests.test_buildkite_agent_token_ambiguous_create",
            CONTRACT.read_text(encoding="utf-8"),
        )
        self.assertIn(
            "/tests/test_buildkite_agent_token_ambiguous_create.py @1-vps",
            CODEOWNERS.read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
