from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOKEN_TOOL = ROOT / ".buildkite/operator/manage-agent-token.py"
CONTRACT = ROOT / ".buildkite/scripts/run-pipeline-contract.sh"
CODEOWNERS = ROOT / ".github/CODEOWNERS"

spec = importlib.util.spec_from_file_location("hostpanel_agent_token_secrets", TOKEN_TOOL)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

CLUSTER_ID = "01234567-89ab-cdef-0123-456789abcdef"
TOKEN_ID = "11111111-2222-3333-4444-555555555555"


def create_args():
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


class BuildkiteAgentTokenSecretsTests(unittest.TestCase):
    def test_create_scope_adds_secret_read_without_widening_revoke_scope(self) -> None:
        self.assertEqual(
            module.REQUIRED_SCOPES,
            frozenset({"read_clusters", "write_clusters"}),
        )
        self.assertEqual(
            module.REQUIRED_CREATE_SCOPES,
            frozenset({"read_clusters", "write_clusters", "read_secret_details"}),
        )
        self.assertNotIn("read_secret_details", module.REQUIRED_SCOPES)

    def test_preflight_enforces_secret_scope_only_when_requested(self) -> None:
        base_scopes = json.dumps(
            {"scopes": ["read_clusters", "write_clusters"]}
        )
        with mock.patch.object(
            module,
            "run_bk",
            side_effect=["{}", "{}", base_scopes],
        ):
            module.preflight("example-org")

        with mock.patch.object(
            module,
            "run_bk",
            side_effect=["{}", "{}", base_scopes],
        ):
            with self.assertRaisesRegex(
                module.OperatorError,
                "read_secret_details",
            ):
                module.preflight(
                    "example-org",
                    required_scopes=module.REQUIRED_CREATE_SCOPES,
                )

    def test_secret_inventory_endpoint_and_empty_shape_are_fail_closed(self) -> None:
        endpoint = module.secret_endpoint("example-org", CLUSTER_ID)
        self.assertEqual(
            endpoint,
            f"/organizations/example-org/clusters/{CLUSTER_ID}/secrets?per_page=100",
        )
        with mock.patch.object(module, "run_bk", return_value="[]"):
            module.verify_no_cluster_secrets("example-org", CLUSTER_ID)

        with mock.patch.object(
            module,
            "run_bk",
            return_value=json.dumps([{"key": "unexpected"}]),
        ):
            with self.assertRaisesRegex(module.OperatorError, "contains Buildkite Secrets"):
                module.verify_no_cluster_secrets("example-org", CLUSTER_ID)

        with mock.patch.object(
            module,
            "run_bk",
            return_value=json.dumps([{"key": f"secret-{i}"} for i in range(100)]),
        ):
            with self.assertRaisesRegex(module.OperatorError, "pagination bound"):
                module.verify_no_cluster_secrets("example-org", CLUSTER_ID)

        with mock.patch.object(module, "run_bk", return_value="{}"):
            with self.assertRaisesRegex(module.OperatorError, "unexpected JSON shape"):
                module.verify_no_cluster_secrets("example-org", CLUSTER_ID)

    def test_create_checks_zero_secrets_before_and_after_post(self) -> None:
        source = TOKEN_TOOL.read_text(encoding="utf-8")
        create = source.index("def create_token")
        scoped_preflight = source.index(
            "preflight(args.org, required_scopes=REQUIRED_CREATE_SCOPES)",
            create,
        )
        cluster = source.index("verify_hostpanel_cluster(cluster_id)", scoped_preflight)
        secret_before = source.index(
            "verify_no_cluster_secrets(args.org, cluster_id)",
            cluster,
        )
        inventory = source.index(
            "if worker_token_matches(cluster_id, description):",
            secret_before,
        )
        post = source.index('["api", "--method", "POST"', inventory)
        validate = source.index("token_id, token = validate_create_response(", post)
        secret_after = source.index(
            "verify_no_cluster_secrets(args.org, cluster_id)",
            validate,
        )
        write = source.index("write_secret_exclusive(output, token)", secret_after)
        self.assertLess(scoped_preflight, cluster)
        self.assertLess(cluster, secret_before)
        self.assertLess(secret_before, inventory)
        self.assertLess(inventory, post)
        self.assertLess(post, validate)
        self.assertLess(validate, secret_after)
        self.assertLess(secret_after, write)

    def test_post_create_secret_drift_revokes_token_before_secret_file_write(self) -> None:
        with mock.patch.object(module, "require_apply_root"), mock.patch.object(
            module, "ensure_token_root"
        ), mock.patch.object(module, "secure_parent"), mock.patch.object(
            module, "preflight"
        ) as preflight, mock.patch.object(
            module, "verify_hostpanel_cluster"
        ), mock.patch.object(
            module,
            "verify_no_cluster_secrets",
            side_effect=[None, module.OperatorError("synthetic secret drift")],
        ) as secrets, mock.patch.object(
            module, "worker_token_matches", return_value=[]
        ), mock.patch.object(
            module, "run_bk", return_value=json.dumps({"id": TOKEN_ID})
        ), mock.patch.object(
            module,
            "validate_create_response",
            return_value=(TOKEN_ID, "opaque-secret-token-12345"),
        ), mock.patch.object(
            module, "revoke_created_token", return_value=True
        ) as revoke, mock.patch.object(
            module, "write_secret_exclusive"
        ) as write_secret:
            with self.assertRaisesRegex(module.OperatorError, "synthetic secret drift"):
                module.create_token(create_args())

        preflight.assert_called_once_with(
            "example-org",
            required_scopes=module.REQUIRED_CREATE_SCOPES,
        )
        self.assertEqual(secrets.call_count, 2)
        revoke.assert_called_once_with(CLUSTER_ID, TOKEN_ID)
        write_secret.assert_not_called()

    def test_revoke_source_keeps_default_narrow_preflight(self) -> None:
        source = TOKEN_TOOL.read_text(encoding="utf-8")
        revoke = source.index("def revoke_token")
        preflight = source.index("preflight(args.org)", revoke)
        identity = source.index("verify_hostpanel_cluster_identity(cluster_id)", preflight)
        self.assertLess(preflight, identity)
        self.assertNotIn(
            "REQUIRED_CREATE_SCOPES",
            source[revoke : source.index("def parser", revoke)],
        )

    def test_pipeline_contract_and_codeowners_cover_secret_race_tests(self) -> None:
        self.assertIn(
            "tests.test_buildkite_agent_token_secrets",
            CONTRACT.read_text(encoding="utf-8"),
        )
        self.assertIn(
            "/tests/test_buildkite_agent_token_secrets.py @1-vps",
            CODEOWNERS.read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
