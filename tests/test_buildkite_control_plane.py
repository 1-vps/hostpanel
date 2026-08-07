from __future__ import annotations

import base64
import contextlib
import importlib.util
import io
import json
import pathlib
import subprocess
import sys
import unittest
from unittest import mock

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
WRAPPER = ROOT / ".buildkite/operator/bootstrap-control-plane.sh"
OPERATOR = ROOT / ".buildkite/operator/bootstrap-control-plane.py"
CONTRACT = ROOT / ".buildkite/scripts/run-pipeline-contract.sh"
CODEOWNERS = ROOT / ".github/CODEOWNERS"

spec = importlib.util.spec_from_file_location("hostpanel_control_plane", OPERATOR)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

CLUSTER_ID = "01234567-89ab-cdef-0123-456789abcdef"
PIPELINE_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
QUEUE_IDS = {
    "hostpanel-upload": "11111111-1111-1111-1111-111111111111",
    "hostpanel-ci": "22222222-2222-2222-2222-222222222222",
    "hostpanel-qemu": "33333333-3333-3333-3333-333333333333",
}


def signature() -> dict:
    header = base64.urlsafe_b64encode(
        json.dumps(
            {"alg": "EdDSA", "kid": "hostpanel-2026-08"}, separators=(",", ":")
        ).encode()
    ).decode().rstrip("=")
    return {
        "algorithm": "EdDSA",
        "signed_fields": ["command", "env", "matrix", "plugins", "repository_url"],
        "value": f"{header}..opaque-signature",
    }


def queue_payload() -> list[dict]:
    return [
        {
            "id": QUEUE_IDS[key],
            "key": key,
            "dispatch_paused": False,
            "hosted": False,
            "hosted_agents": None,
        }
        for key in ("hostpanel-upload", "hostpanel-ci", "hostpanel-qemu")
    ]


def pipeline_fixture(*, active: bool = False, signed: bool = False, webhook: bool = False) -> dict:
    step = yaml.safe_load(module.STATIC_BOOTSTRAP)["steps"][0]
    if signed:
        step["signature"] = signature()
    provider = dict(module.ACTIVE_PROVIDER if active else module.QUARANTINE_PROVIDER)
    provider["repository"] = module.GITHUB_REPOSITORY
    return {
        "id": PIPELINE_ID,
        "name": module.PIPELINE_NAME,
        "slug": module.PIPELINE_SLUG,
        "repository": module.REPOSITORY,
        "cluster_id": CLUSTER_ID,
        "branch_configuration": "main",
        "default_branch": "main",
        "visibility": "private",
        "env": {},
        "allow_rebuilds": False,
        "archived_at": None,
        "pipeline_template_uuid": None,
        "skip_queued_branch_builds": False,
        "skip_queued_branch_builds_filter": None,
        "cancel_running_branch_builds": False,
        "cancel_running_branch_builds_filter": None,
        "provider": {
            "id": "github",
            "settings": provider,
            "webhook_url": (
                "https://webhook.buildkite.com/deliver/abcDEF0123_-"
                if webhook
                else ""
            ),
        },
        "configuration": yaml.safe_dump({"steps": [step]}, sort_keys=False),
    }


def access_token() -> str:
    return json.dumps({"scopes": sorted(module.REQUIRED_SCOPES)})


class BuildkiteControlPlaneTests(unittest.TestCase):
    def run_wrapper(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(WRAPPER), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def test_creation_plan_is_read_only_without_bk(self) -> None:
        result = self.run_wrapper("--org", "example-org")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("control-plane creation plan", result.stdout)
        self.assertIn("trigger_mode=none", result.stdout)
        self.assertIn("MANDATORY STOP", result.stdout)
        self.assertNotIn("bk CLI is unavailable", result.stderr)

    def test_activation_plan_is_read_only_and_slug_pinned(self) -> None:
        result = self.run_wrapper("--org", "example-org", "--enable-webhook", "hostpanel")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("activation plan", result.stdout)
        self.assertIn("Cryptographic signature validity is enforced by Buildkite agents", result.stdout)
        bad = self.run_wrapper("--org", "example-org", "--enable-webhook", "other")
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn("reviewed hostpanel pipeline slug", bad.stderr)
        self.assertNotIn("bk CLI is unavailable", bad.stderr)

    def test_apply_confirmation_gates_precede_bk(self) -> None:
        create = self.run_wrapper("--org", "example-org", "--apply")
        self.assertNotEqual(create.returncode, 0)
        self.assertIn("--confirm-create", create.stderr)
        self.assertNotIn("bk CLI is unavailable", create.stderr)
        activate = self.run_wrapper(
            "--org", "example-org", "--enable-webhook", "hostpanel", "--apply"
        )
        self.assertNotEqual(activate.returncode, 0)
        self.assertIn("--confirm-static-bootstrap-signed", activate.stderr)

    def test_pipeline_payload_pins_all_lifecycle_controls(self) -> None:
        payload = json.loads(module.pipeline_payload(CLUSTER_ID))
        self.assertEqual(payload["name"], "HostPanel")
        self.assertEqual(payload["slug"], "hostpanel")
        self.assertEqual(payload["repository"], "git@github.com:1-vps/hostpanel.git")
        self.assertEqual(payload["cluster_id"], CLUSTER_ID)
        self.assertEqual(payload["default_branch"], "main")
        self.assertEqual(payload["branch_configuration"], "main")
        self.assertEqual(payload["visibility"], "private")
        self.assertEqual(payload["env"], {})
        self.assertFalse(payload["allow_rebuilds"])
        self.assertIsNone(payload["pipeline_template_uuid"])
        self.assertFalse(payload["skip_queued_branch_builds"])
        self.assertIsNone(payload["skip_queued_branch_builds_filter"])
        self.assertFalse(payload["cancel_running_branch_builds"])
        self.assertIsNone(payload["cancel_running_branch_builds_filter"])
        self.assertEqual(payload["provider_settings"], module.QUARANTINE_PROVIDER)

    def test_provider_policy_rejects_unknown_future_true_flag(self) -> None:
        payload = pipeline_fixture()
        payload["provider"]["settings"]["future_trigger"] = True
        with self.assertRaises(module.OperatorError):
            module.verify_provider_policy(payload, module.QUARANTINE_PROVIDER)

    def test_provider_policy_keeps_forks_and_merge_commits_off(self) -> None:
        self.assertFalse(module.ACTIVE_PROVIDER["build_pull_request_forks"])
        self.assertFalse(module.ACTIVE_PROVIDER["build_pull_request_merge_commits"])
        self.assertTrue(module.ACTIVE_PROVIDER["build_pull_requests"])
        self.assertTrue(module.ACTIVE_PROVIDER["build_pull_request_ready_for_review"])
        self.assertTrue(module.ACTIVE_PROVIDER["build_issue_comment_created"])
        self.assertEqual(module.ACTIVE_PROVIDER["issue_comment_command_word"], "/bk")
        self.assertEqual(module.ACTIVE_PROVIDER["issue_comment_match_mode"], "exact")

    def test_scope_preflight_requires_every_reviewed_scope(self) -> None:
        calls: list[list[str]] = []

        def fake(args: list[str]) -> str:
            calls.append(args)
            if args == ["api", "/access-token"]:
                return access_token()
            return "{}"

        with mock.patch.object(module, "run_bk", side_effect=fake):
            module.preflight("example-org")
        self.assertEqual(calls[:2], [["auth", "switch", "example-org"], ["auth", "status", "-o", "json"]])
        self.assertEqual(calls[2], ["api", "/access-token"])

    def test_duplicate_cluster_inventory_saturation_fails_closed(self) -> None:
        with mock.patch.object(
            module,
            "run_bk",
            return_value=json.dumps([{"name": f"cluster-{i}"} for i in range(100)]),
        ):
            with self.assertRaises(module.OperatorError):
                module.assert_no_existing_control_plane()

    def test_duplicate_pipeline_name_slug_and_repository_are_all_guarded(self) -> None:
        cluster_response = "[]"
        for item in (
            {"name": "HostPanel", "slug": "other", "repository": "x"},
            {"name": "Other", "slug": "hostpanel", "repository": "x"},
            {"name": "Other", "slug": "other", "repository": module.REPOSITORY},
        ):
            with self.subTest(item=item):
                with mock.patch.object(
                    module,
                    "run_bk",
                    side_effect=[cluster_response, json.dumps([item])],
                ):
                    with self.assertRaises(module.OperatorError):
                        module.assert_no_existing_control_plane()

    def test_queue_inventory_requires_exact_self_hosted_unpaused_three(self) -> None:
        mapping = module.parse_queue_inventory(json.dumps(queue_payload()))
        self.assertEqual(mapping, QUEUE_IDS)
        for mutation in ("paused", "hosted", "extra"):
            queues = queue_payload()
            if mutation == "paused":
                queues[0]["dispatch_paused"] = True
            elif mutation == "hosted":
                queues[0]["hosted"] = True
                queues[0]["hosted_agents"] = {}
            else:
                queues.append(
                    {
                        "id": "44444444-4444-4444-4444-444444444444",
                        "key": "other",
                        "dispatch_paused": False,
                    }
                )
            with self.subTest(mutation=mutation), self.assertRaises(module.OperatorError):
                module.parse_queue_inventory(json.dumps(queues))

    def test_canonical_uuid_rejects_uppercase_and_noncanonical_forms(self) -> None:
        self.assertEqual(module.canonical_uuid(CLUSTER_ID, "cluster"), CLUSTER_ID)
        with self.assertRaises(module.OperatorError):
            module.canonical_uuid(CLUSTER_ID.upper(), "cluster")
        with self.assertRaises(module.OperatorError):
            module.canonical_uuid("{01234567-89ab-cdef-0123-456789abcdef}", "cluster")

    def test_create_control_plane_happy_path_is_fully_reverified(self) -> None:
        calls: list[list[str]] = []

        def fake(args: list[str]) -> str:
            calls.append(args)
            if args[:2] == ["auth", "switch"] or args[:2] == ["auth", "status"]:
                return "{}"
            if args == ["api", "/access-token"]:
                return access_token()
            if args == ["api", "/clusters?per_page=100"]:
                return "[]"
            if args[:2] == ["pipeline", "list"]:
                return "[]"
            if args[:2] == ["cluster", "create"]:
                return json.dumps({"id": CLUSTER_ID})
            if args[:2] == ["queue", "create"]:
                key = args[args.index("--key") + 1]
                return json.dumps({"id": QUEUE_IDS[key]})
            if args[:2] == ["cluster", "view"]:
                return json.dumps({"id": CLUSTER_ID, "name": "HostPanel", "default_queue_id": None})
            if args[:2] == ["queue", "list"]:
                return json.dumps(queue_payload())
            if args[0] == "api" and args[1].startswith("/organizations/example-org/agents?"):
                return "[]"
            if args[:4] == ["api", "--method", "POST", "/pipelines"]:
                body = json.loads(args[args.index("--data") + 1])
                self.assertIsNone(body["pipeline_template_uuid"])
                self.assertFalse(body["allow_rebuilds"])
                return json.dumps({"id": PIPELINE_ID, "slug": "hostpanel"})
            if args[:2] == ["pipeline", "view"]:
                return json.dumps(pipeline_fixture())
            if args[:2] == ["build", "list"]:
                return "[]"
            raise AssertionError(f"unexpected fake bk call: {args}")

        with mock.patch.object(module, "run_bk", side_effect=fake):
            resources = module.create_control_plane("example-org")
        self.assertEqual(resources.cluster_uuid, CLUSTER_ID)
        self.assertEqual(resources.pipeline_uuid, PIPELINE_ID)
        self.assertEqual(resources.pipeline_slug, "hostpanel")
        self.assertEqual(resources.upload_queue_uuid, QUEUE_IDS["hostpanel-upload"])
        self.assertTrue(any(call[:4] == ["api", "--method", "POST", "/pipelines"] for call in calls))
        self.assertFalse(any("DELETE" in call for call in calls))

    def test_partial_create_reports_identifiers_and_never_auto_deletes(self) -> None:
        calls: list[list[str]] = []

        def fake(args: list[str]) -> str:
            calls.append(args)
            if args[:2] == ["auth", "switch"] or args[:2] == ["auth", "status"]:
                return "{}"
            if args == ["api", "/access-token"]:
                return access_token()
            if args == ["api", "/clusters?per_page=100"]:
                return "[]"
            if args[:2] == ["pipeline", "list"]:
                return "[]"
            if args[:2] == ["cluster", "create"]:
                return json.dumps({"id": CLUSTER_ID})
            if args[:2] == ["queue", "create"]:
                key = args[args.index("--key") + 1]
                if key == "hostpanel-ci":
                    raise module.OperatorError("synthetic queue failure")
                return json.dumps({"id": QUEUE_IDS[key]})
            raise AssertionError(f"unexpected fake bk call: {args}")

        stderr = io.StringIO()
        with mock.patch.object(module, "run_bk", side_effect=fake), contextlib.redirect_stderr(stderr):
            with self.assertRaises(module.OperatorError):
                module.create_control_plane("example-org")
        self.assertIn(f"cluster_uuid={CLUSTER_ID}", stderr.getvalue())
        self.assertIn(f"upload_queue_uuid={QUEUE_IDS['hostpanel-upload']}", stderr.getvalue())
        self.assertIn("No automatic cleanup was attempted", stderr.getvalue())
        self.assertFalse(any("DELETE" in call for call in calls))

    def test_activation_orders_webhook_before_active_patch_and_reverifies(self) -> None:
        calls: list[list[str]] = []
        state = {"webhook": False, "active": False}

        def fake(args: list[str]) -> str:
            calls.append(args)
            if args[:2] == ["auth", "switch"] or args[:2] == ["auth", "status"]:
                return "{}"
            if args == ["api", "/access-token"]:
                return access_token()
            if args[:2] == ["pipeline", "view"]:
                return json.dumps(
                    pipeline_fixture(active=state["active"], signed=True, webhook=state["webhook"])
                )
            if args[:2] == ["cluster", "view"]:
                return json.dumps({"id": CLUSTER_ID, "name": "HostPanel", "default_queue_id": None})
            if args[:2] == ["queue", "list"]:
                return json.dumps(queue_payload())
            if args[0] == "api" and args[1].startswith("/organizations/example-org/agents?"):
                return "[]"
            if args[:2] == ["build", "list"]:
                return "[]"
            if args == ["api", "--method", "POST", "/pipelines/hostpanel/webhook"]:
                state["webhook"] = True
                return "{}"
            if args[:4] == ["api", "--method", "PATCH", "/pipelines/hostpanel"]:
                patch = json.loads(args[args.index("--data") + 1])
                self.assertEqual(patch, {"provider_settings": module.ACTIVE_PROVIDER})
                self.assertTrue(state["webhook"])
                state["active"] = True
                return "{}"
            raise AssertionError(f"unexpected fake bk call: {args}")

        with mock.patch.object(module, "run_bk", side_effect=fake):
            module.activate_control_plane("example-org")
        webhook_index = calls.index(["api", "--method", "POST", "/pipelines/hostpanel/webhook"])
        patch_index = next(i for i, call in enumerate(calls) if call[:4] == ["api", "--method", "PATCH", "/pipelines/hostpanel"])
        self.assertLess(webhook_index, patch_index)
        self.assertTrue(state["active"])

    def test_pipeline_verification_binds_provider_repo_template_webhook_and_signed_fields(self) -> None:
        module.verify_pipeline(
            json.dumps(pipeline_fixture(signed=True, webhook=True)),
            cluster_id=CLUSTER_ID,
            expected_provider=module.QUARANTINE_PROVIDER,
            require_signature=True,
            require_webhook=True,
        )
        mutations = []
        wrong_repo = pipeline_fixture(signed=True, webhook=True)
        wrong_repo["provider"]["settings"]["repository"] = "other/repo"
        mutations.append(wrong_repo)
        template = pipeline_fixture(signed=True, webhook=True)
        template["pipeline_template_uuid"] = "bbbbbbbb-cccc-dddd-eeee-ffffffffffff"
        mutations.append(template)
        webhook = pipeline_fixture(signed=True, webhook=True)
        webhook["provider"]["webhook_url"] += "?redirect=evil"
        mutations.append(webhook)
        for payload in mutations:
            with self.assertRaises(module.OperatorError):
                module.verify_pipeline(
                    json.dumps(payload),
                    cluster_id=CLUSTER_ID,
                    expected_provider=module.QUARANTINE_PROVIDER,
                    require_signature=True,
                    require_webhook=True,
                )

    def test_wrapper_and_operator_keep_forbidden_agent_actions_out(self) -> None:
        text = WRAPPER.read_text(encoding="utf-8") + OPERATOR.read_text(encoding="utf-8")
        self.assertTrue(WRAPPER.read_text(encoding="utf-8").startswith("#!/usr/bin/env bash\nset -euo pipefail\n"))
        for forbidden in (
            "bk auth token",
            "buildkite-agent start",
            "systemctl start",
            "systemctl restart",
            "agent-token",
            "/github-webhooks",
        ):
            self.assertNotIn(forbidden, text)
        self.assertIn("exec python3", WRAPPER.read_text(encoding="utf-8"))

    def test_pipeline_contract_and_codeowners_cover_control_plane(self) -> None:
        self.assertIn("tests.test_buildkite_control_plane", CONTRACT.read_text(encoding="utf-8"))
        self.assertIn("/tests/test_buildkite_control_plane.py @1-vps", CODEOWNERS.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
