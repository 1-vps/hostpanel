from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOKEN_TOOL = ROOT / ".buildkite/operator/manage-agent-token.py"
CONTRACT = ROOT / ".buildkite/scripts/run-pipeline-contract.sh"
CODEOWNERS = ROOT / ".github/CODEOWNERS"

spec = importlib.util.spec_from_file_location("hostpanel_agent_token_operator", TOKEN_TOOL)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class BuildkiteAgentTokenOperatorTests(unittest.TestCase):
    def run_tool(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(TOKEN_TOOL), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def test_create_plan_is_read_only_and_exact_ip(self) -> None:
        result = self.run_tool(
            "create",
            "--org",
            "example-org",
            "--cluster-id",
            "01234567-89ab-cdef-0123-456789abcdef",
            "--worker-id",
            "ci-01",
            "--allowed-ip",
            "192.0.2.10",
            "--output-file",
            "/persistent/example/token",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("No Buildkite API writes are performed in plan mode.", result.stdout)
        self.assertIn("allowed_ip_addresses=192.0.2.10/32", result.stdout)
        self.assertIn("ttl_minutes=15", result.stdout)
        self.assertIn("root-owned tmpfs", result.stdout)
        self.assertIn("never prints the token value", result.stdout)

    def test_create_apply_requires_confirmation_before_root_or_bk(self) -> None:
        result = self.run_tool(
            "create",
            "--org",
            "example-org",
            "--cluster-id",
            "01234567-89ab-cdef-0123-456789abcdef",
            "--worker-id",
            "ci-01",
            "--allowed-ip",
            "192.0.2.10",
            "--output-file",
            "/run/hostpanel-token/token",
            "--apply",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--confirm-create", result.stderr)
        self.assertNotIn("apply mode must run as root", result.stderr)
        self.assertNotIn("bk CLI is unavailable", result.stderr)

    def test_revoke_apply_requires_confirmation_before_root_or_bk(self) -> None:
        result = self.run_tool(
            "revoke",
            "--org",
            "example-org",
            "--cluster-id",
            "01234567-89ab-cdef-0123-456789abcdef",
            "--token-id",
            "11111111-2222-3333-4444-555555555555",
            "--apply",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--confirm-revoke", result.stderr)
        self.assertNotIn("apply mode must run as root", result.stderr)
        self.assertNotIn("bk CLI is unavailable", result.stderr)

    def test_apply_requires_root(self) -> None:
        with mock.patch.object(module.os, "geteuid", return_value=1000):
            with self.assertRaises(module.OperatorError):
                module.require_apply_root()

    def test_allowed_ip_rejects_ranges_and_ipv6(self) -> None:
        for value in ("192.0.2.0/24", "2001:db8::1"):
            with self.assertRaises(module.OperatorError):
                module.exact_ipv4_cidr(value)
        self.assertEqual(module.exact_ipv4_cidr("192.0.2.7"), "192.0.2.7/32")

    def test_ttl_bounds_are_fail_closed(self) -> None:
        source = TOKEN_TOOL.read_text(encoding="utf-8")
        self.assertIn("MIN_TTL_MINUTES = 15", source)
        self.assertIn("MAX_TTL_MINUTES = 60", source)
        self.assertIn("--ttl-minutes must be between", source)

    def test_create_response_requires_exact_metadata_and_safe_opaque_token(self) -> None:
        expires = dt.datetime(2026, 8, 7, 19, 0, tzinfo=dt.timezone.utc)
        payload = {
            "id": "11111111-2222-3333-4444-555555555555",
            "description": "hostpanel-worker:ci-01",
            "allowed_ip_addresses": "192.0.2.10/32",
            "expires_at": "2026-08-07T19:00:00Z",
            "token": "igo6HEj5fxQbgBTDoDzNaZzT",
        }
        token_id, token = module.validate_create_response(
            payload,
            description="hostpanel-worker:ci-01",
            allowed_cidr="192.0.2.10/32",
            expires_at=expires,
        )
        self.assertEqual(token_id, payload["id"])
        self.assertEqual(token, payload["token"])
        for bad_token in (
            "short",
            "token with whitespace here",
            "é" * 24,
            "x" * 513,
        ):
            changed = dict(payload)
            changed["token"] = bad_token
            with self.assertRaises(module.OperatorError):
                module.validate_create_response(
                    changed,
                    description="hostpanel-worker:ci-01",
                    allowed_cidr="192.0.2.10/32",
                    expires_at=expires,
                )
        for key, bad in (
            ("allowed_ip_addresses", "0.0.0.0/0"),
            ("description", "shared-token"),
        ):
            changed = dict(payload)
            changed[key] = bad
            with self.assertRaises(module.OperatorError):
                module.validate_create_response(
                    changed,
                    description="hostpanel-worker:ci-01",
                    allowed_cidr="192.0.2.10/32",
                    expires_at=expires,
                )

    def test_write_all_retries_short_writes(self) -> None:
        read_fd, write_fd = os.pipe()
        original_write = os.write
        calls = 0

        def partial(fd: int, data) -> int:
            nonlocal calls
            calls += 1
            raw = bytes(data)
            if calls == 1 and len(raw) > 3:
                return original_write(fd, raw[:3])
            return original_write(fd, raw)

        try:
            with mock.patch.object(module.os, "write", side_effect=partial):
                module.write_all(write_fd, b"opaque-secret-token-12345")
            os.close(write_fd)
            write_fd = -1
            self.assertEqual(os.read(read_fd, 100), b"opaque-secret-token-12345")
            self.assertGreaterEqual(calls, 2)
        finally:
            os.close(read_fd)
            if write_fd >= 0:
                os.close(write_fd)

    def test_write_all_rejects_zero_progress(self) -> None:
        with mock.patch.object(module.os, "write", return_value=0):
            with self.assertRaises(module.OperatorError):
                module.write_all(1, b"opaque-secret-token-12345")

    def test_secret_file_write_is_exclusive_and_length_checked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.chmod(tmp, 0o700)
            parent_fd = os.open(tmp, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            path = pathlib.Path(tmp) / "token"
            real_fstat = os.fstat

            def root_fstat(fd: int):
                info = real_fstat(fd)
                values = list(info)
                values[4] = 0
                return os.stat_result(values)

            try:
                with mock.patch.object(
                    module, "open_secure_parent", side_effect=lambda _p: os.dup(parent_fd)
                ), mock.patch.object(module.os, "fstat", side_effect=root_fstat):
                    module.write_secret_exclusive(path, "opaque-secret-token-12345")
                self.assertEqual(path.read_bytes(), b"opaque-secret-token-12345")
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            finally:
                os.close(parent_fd)

    def test_secure_parent_requires_root_owned_0700_tmpfs(self) -> None:
        source = TOKEN_TOOL.read_text(encoding="utf-8")
        self.assertIn('stat.S_IMODE(info.st_mode) != 0o700', source)
        self.assertIn('fd_filesystem_type(fd) != "tmpfs"', source)
        self.assertIn("info.st_uid != 0", source)
        self.assertIn("O_NOFOLLOW", source)
        self.assertIn("O_DIRECTORY", source)

    def test_hostpanel_cluster_identity_and_queue_shape_are_exact(self) -> None:
        cluster_id = "01234567-89ab-cdef-0123-456789abcdef"
        responses = [
            json.dumps({"id": cluster_id, "name": "HostPanel", "default_queue_id": None}),
            json.dumps(
                [
                    {
                        "id": "11111111-1111-1111-1111-111111111111",
                        "key": "hostpanel-upload",
                        "dispatch_paused": False,
                        "hosted": False,
                        "hosted_agents": None,
                    },
                    {
                        "id": "22222222-2222-2222-2222-222222222222",
                        "key": "hostpanel-ci",
                        "dispatch_paused": False,
                        "hosted": False,
                        "hosted_agents": None,
                    },
                    {
                        "id": "33333333-3333-3333-3333-333333333333",
                        "key": "hostpanel-qemu",
                        "dispatch_paused": False,
                        "hosted": False,
                        "hosted_agents": None,
                    },
                ]
            ),
        ]
        with mock.patch.object(module, "run_bk", side_effect=responses) as run:
            module.verify_hostpanel_cluster(cluster_id)
        self.assertEqual(run.call_args_list[0].args[0], ["api", f"/clusters/{cluster_id}"])
        self.assertIn("per_page=100", run.call_args_list[1].args[0][1])

    def test_wrong_cluster_name_is_rejected(self) -> None:
        cluster_id = "01234567-89ab-cdef-0123-456789abcdef"
        with mock.patch.object(
            module,
            "run_bk",
            return_value=json.dumps(
                {"id": cluster_id, "name": "Other", "default_queue_id": None}
            ),
        ):
            with self.assertRaises(module.OperatorError):
                module.verify_hostpanel_cluster(cluster_id)

    def test_hosted_or_paused_queue_is_rejected(self) -> None:
        cluster_id = "01234567-89ab-cdef-0123-456789abcdef"
        cluster = json.dumps(
            {"id": cluster_id, "name": "HostPanel", "default_queue_id": None}
        )
        for bad_queue in (
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "key": "hostpanel-upload",
                "dispatch_paused": True,
                "hosted_agents": None,
            },
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "key": "hostpanel-upload",
                "dispatch_paused": False,
                "hosted": True,
                "hosted_agents": {"instance_shape": {}},
            },
        ):
            queues = [
                bad_queue,
                {
                    "id": "22222222-2222-2222-2222-222222222222",
                    "key": "hostpanel-ci",
                    "dispatch_paused": False,
                    "hosted_agents": None,
                },
                {
                    "id": "33333333-3333-3333-3333-333333333333",
                    "key": "hostpanel-qemu",
                    "dispatch_paused": False,
                    "hosted_agents": None,
                },
            ]
            with mock.patch.object(
                module, "run_bk", side_effect=[cluster, json.dumps(queues)]
            ):
                with self.assertRaises(module.OperatorError):
                    module.verify_hostpanel_cluster(cluster_id)

    def test_cluster_verification_precedes_token_inventory_and_write(self) -> None:
        source = TOKEN_TOOL.read_text(encoding="utf-8")
        create = source.index("def create_token")
        cluster = source.index("verify_hostpanel_cluster(cluster_id)", create)
        inventory = source.index("for item in list_tokens(cluster_id):", cluster)
        post = source.index('"--method", "POST"', inventory)
        self.assertLess(cluster, inventory)
        self.assertLess(inventory, post)

    def test_revoke_refuses_non_hostpanel_descriptions(self) -> None:
        source = TOKEN_TOOL.read_text(encoding="utf-8")
        self.assertIn('DESCRIPTION_PREFIX = "hostpanel-worker:"', source)
        self.assertIn(
            "refusing to revoke a token not created for a HostPanel per-worker registration",
            source,
        )

    def test_sensitive_create_response_is_never_echoed_and_failure_revokes(self) -> None:
        source = TOKEN_TOOL.read_text(encoding="utf-8")
        self.assertIn("sensitive_response=True", source)
        self.assertIn("revoke_created_token(cluster_id, token_id)", source)
        self.assertNotIn("print(token)", source)
        self.assertNotIn("print(raw)", source)

    def test_api_contract_matches_cluster_token_endpoints(self) -> None:
        source = TOKEN_TOOL.read_text(encoding="utf-8")
        for expected in (
            'base = f"/clusters/{cluster_id}/tokens"',
            'return f"/clusters/{cluster_id}/queues?per_page={MAX_INVENTORY_ITEMS}"',
            '"expires_at": isoformat_z(expires)',
            '"allowed_ip_addresses": allowed_cidr',
            '["api", "--method", "POST", token_endpoint(cluster_id), "--data", payload]',
            '["api", "--method", "DELETE", token_endpoint(cluster_id, token_id)]',
            'REQUIRED_SCOPES = frozenset({"read_clusters", "write_clusters"})',
        ):
            self.assertIn(expected, source)

    def test_explicit_revoke_verifies_absence_before_local_file_cleanup(self) -> None:
        source = TOKEN_TOOL.read_text(encoding="utf-8")
        revoke_start = source.index("def revoke_token")
        delete = source.index('["api", "--method", "DELETE"', revoke_start)
        absence = source.index("if not token_is_absent(cluster_id, token_id)", delete)
        cleanup = source.index("safe_remove_token_file(token_file)", absence)
        self.assertLess(delete, absence)
        self.assertLess(absence, cleanup)

    def test_automatic_create_rollback_also_verifies_absence(self) -> None:
        source = TOKEN_TOOL.read_text(encoding="utf-8")
        rollback_start = source.index("def revoke_created_token")
        delete = source.index('["api", "--method", "DELETE"', rollback_start)
        absence = source.index("return token_is_absent(cluster_id, token_id)", delete)
        create_start = source.index("def create_token")
        failure = source.index("revocation/absence verification also failed", create_start)
        self.assertLess(delete, absence)
        self.assertGreater(failure, create_start)

    def test_token_inventory_is_bounded_and_fails_closed_on_pagination(self) -> None:
        source = TOKEN_TOOL.read_text(encoding="utf-8")
        self.assertIn('f"?per_page={MAX_INVENTORY_ITEMS}"', source)
        self.assertIn("if len(payload) >= MAX_INVENTORY_ITEMS:", source)
        self.assertIn("cannot prove inventory completeness", source)

    def test_successful_create_with_unrecoverable_id_demands_manual_cleanup(self) -> None:
        source = TOKEN_TOOL.read_text(encoding="utf-8")
        self.assertIn(
            "token create returned success but its token id could not be safely recovered",
            source,
        )
        self.assertIn("inspect cluster tokens for description", source)
        self.assertIn("revoke any match", source)

    def test_apply_environment_does_not_inherit_xdg_paths(self) -> None:
        source = TOKEN_TOOL.read_text(encoding="utf-8")
        safe_env = source[source.index("def safe_environment") : source.index("def bk_binary")]
        self.assertNotIn("XDG_CONFIG_HOME", safe_env)
        self.assertNotIn("XDG_DATA_HOME", safe_env)
        self.assertIn('"HOME": trusted_home()', safe_env)

    def test_pipeline_contract_and_codeowners_cover_operator(self) -> None:
        self.assertIn(
            "tests.test_buildkite_agent_token_operator",
            CONTRACT.read_text(encoding="utf-8"),
        )
        self.assertIn(
            "/tests/test_buildkite_agent_token_operator.py @1-vps",
            CODEOWNERS.read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
