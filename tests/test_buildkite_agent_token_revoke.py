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

spec = importlib.util.spec_from_file_location("hostpanel_agent_token_revoke", TOKEN_TOOL)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

CLUSTER_ID = "01234567-89ab-cdef-0123-456789abcdef"
TOKEN_ID = "11111111-2222-3333-4444-555555555555"


def args(*, token_file: str | None = None):
    return SimpleNamespace(
        org="example-org",
        cluster_id=CLUSTER_ID,
        token_id=TOKEN_ID,
        token_file=token_file,
        apply=True,
        confirm_revoke=True,
    )


class BuildkiteAgentTokenRevokeTests(unittest.TestCase):
    def test_uuid_parser_rejects_uppercase_noncanonical_value(self) -> None:
        self.assertEqual(module.canonical_uuid(CLUSTER_ID, "cluster"), CLUSTER_ID)
        with self.assertRaises(module.OperatorError):
            module.canonical_uuid(CLUSTER_ID.upper(), "cluster")

    def test_cluster_identity_does_not_require_default_or_queue_state(self) -> None:
        payload = '{"id":"%s","name":"HostPanel","default_queue_id":"drifted"}' % CLUSTER_ID
        with mock.patch.object(module, "run_bk", return_value=payload) as run:
            actual = module.verify_hostpanel_cluster_identity(CLUSTER_ID)
        self.assertEqual(actual["id"], CLUSTER_ID)
        run.assert_called_once_with(["api", f"/clusters/{CLUSTER_ID}"])

    def test_create_path_still_requires_no_default_queue_and_exact_queue_shape(self) -> None:
        payload = '{"id":"%s","name":"HostPanel","default_queue_id":"drifted"}' % CLUSTER_ID
        with mock.patch.object(module, "run_bk", return_value=payload):
            with self.assertRaises(module.OperatorError):
                module.verify_hostpanel_cluster(CLUSTER_ID)

    def test_revoke_already_absent_is_idempotent_and_never_deletes(self) -> None:
        with mock.patch.object(module, "require_apply_root"), mock.patch.object(
            module, "ensure_token_root"
        ), mock.patch.object(module, "preflight"), mock.patch.object(
            module, "verify_hostpanel_cluster_identity"
        ) as identity_check, mock.patch.object(
            module, "verify_hostpanel_cluster", side_effect=AssertionError("queue verifier must not run")
        ), mock.patch.object(module, "list_tokens", return_value=[]), mock.patch.object(
            module, "run_bk", side_effect=AssertionError("DELETE must not run")
        ):
            result = module.revoke_token(args())
        self.assertEqual(result, 0)
        identity_check.assert_called_once_with(CLUSTER_ID)

    def test_revoke_existing_token_ignores_queue_drift_and_proves_absence(self) -> None:
        listed = {"id": TOKEN_ID, "description": "hostpanel-worker:ci-01"}
        delete_calls: list[list[str]] = []

        def run_bk(command, *, sensitive_response=False):
            delete_calls.append(command)
            self.assertTrue(sensitive_response)
            return ""

        with mock.patch.object(module, "require_apply_root"), mock.patch.object(
            module, "ensure_token_root"
        ), mock.patch.object(module, "preflight"), mock.patch.object(
            module, "verify_hostpanel_cluster_identity"
        ), mock.patch.object(
            module, "verify_hostpanel_cluster", side_effect=AssertionError("queue verifier must not run")
        ), mock.patch.object(module, "list_tokens", side_effect=[[listed], []]), mock.patch.object(
            module, "run_bk", side_effect=run_bk
        ):
            result = module.revoke_token(args())
        self.assertEqual(result, 0)
        self.assertEqual(
            delete_calls,
            [["api", "--method", "DELETE", f"/clusters/{CLUSTER_ID}/tokens/{TOKEN_ID}"]],
        )

    def test_revoke_refuses_foreign_description_before_delete(self) -> None:
        listed = {"id": TOKEN_ID, "description": "shared-admin-token"}
        with mock.patch.object(module, "require_apply_root"), mock.patch.object(
            module, "ensure_token_root"
        ), mock.patch.object(module, "preflight"), mock.patch.object(
            module, "verify_hostpanel_cluster_identity"
        ), mock.patch.object(module, "list_tokens", return_value=[listed]), mock.patch.object(
            module, "run_bk", side_effect=AssertionError("DELETE must not run")
        ):
            with self.assertRaises(module.OperatorError):
                module.revoke_token(args())

    def test_already_absent_token_cleans_local_tmpfs_file_when_requested(self) -> None:
        token_file = "/run/hostpanel-buildkite-tokens/ci-01.token"
        with mock.patch.object(module, "require_apply_root"), mock.patch.object(
            module, "ensure_token_root"
        ), mock.patch.object(module, "open_secure_parent", return_value=99), mock.patch.object(
            module.os, "close"
        ), mock.patch.object(module, "preflight"), mock.patch.object(
            module, "verify_hostpanel_cluster_identity"
        ), mock.patch.object(module, "list_tokens", return_value=[]), mock.patch.object(
            module, "safe_remove_token_file"
        ) as cleanup:
            result = module.revoke_token(args(token_file=token_file))
        self.assertEqual(result, 0)
        cleanup.assert_called_once_with(pathlib.Path(token_file))

    def test_pipeline_contract_and_codeowners_cover_revoke_tests(self) -> None:
        self.assertIn(
            "tests.test_buildkite_agent_token_revoke",
            CONTRACT.read_text(encoding="utf-8"),
        )
        self.assertIn(
            "/tests/test_buildkite_agent_token_revoke.py @1-vps",
            CODEOWNERS.read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
