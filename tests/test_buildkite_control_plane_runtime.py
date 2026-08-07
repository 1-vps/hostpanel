from __future__ import annotations

import base64
import importlib.util
import json
import pathlib
import sys
import unittest
from unittest import mock

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".buildkite/operator/control-plane-runtime.py"
CONTRACT = ROOT / ".buildkite/scripts/run-pipeline-contract.sh"
CODEOWNERS = ROOT / ".github/CODEOWNERS"

spec = importlib.util.spec_from_file_location("hostpanel_control_plane_runtime", RUNTIME)
assert spec and spec.loader
runtime = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = runtime
spec.loader.exec_module(runtime)
base = runtime.base

PIPELINE_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
CLUSTER_ID = "01234567-89ab-cdef-0123-456789abcdef"
QUEUE_IDS = {
    "hostpanel-upload": "11111111-1111-1111-1111-111111111111",
    "hostpanel-ci": "22222222-2222-2222-2222-222222222222",
    "hostpanel-qemu": "33333333-3333-3333-3333-333333333333",
}


def identity():
    return base.ActivationIdentity(
        pipeline_uuid=PIPELINE_ID,
        cluster_uuid=CLUSTER_ID,
        upload_queue_uuid=QUEUE_IDS["hostpanel-upload"],
        ci_queue_uuid=QUEUE_IDS["hostpanel-ci"],
        qemu_queue_uuid=QUEUE_IDS["hostpanel-qemu"],
    )


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


def pipeline(*, provider: dict | None = None, signed: bool = True, webhook: bool = True) -> dict:
    step = yaml.safe_load(base.STATIC_BOOTSTRAP)["steps"][0]
    if signed:
        step["signature"] = signature()
    settings = dict(provider if provider is not None else base.QUARANTINE_PROVIDER)
    settings["repository"] = base.GITHUB_REPOSITORY
    return {
        "id": PIPELINE_ID,
        "name": base.PIPELINE_NAME,
        "slug": base.PIPELINE_SLUG,
        "repository": base.REPOSITORY,
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
            "settings": settings,
            "webhook_url": (
                "https://webhook.buildkite.com/deliver/abcDEF0123_-" if webhook else ""
            ),
        },
        "configuration": yaml.safe_dump({"steps": [step]}, sort_keys=False),
    }


class BuildkiteControlPlaneRuntimeTests(unittest.TestCase):
    def test_pipeline_fetch_uses_rest_get_not_cli_projection(self) -> None:
        with mock.patch.object(base, "run_bk", return_value="{}") as run:
            runtime.fetch_pipeline_raw()
        run.assert_called_once_with(["api", "/pipelines/hostpanel"])

    def test_lifecycle_patch_normalizes_every_mutable_control(self) -> None:
        payload = json.loads(runtime.pipeline_state_patch(base.QUARANTINE_PROVIDER))
        self.assertEqual(payload["branch_configuration"], "main")
        self.assertEqual(payload["default_branch"], "main")
        self.assertEqual(payload["visibility"], "private")
        self.assertEqual(payload["env"], {})
        self.assertFalse(payload["allow_rebuilds"])
        self.assertIsNone(payload["pipeline_template_uuid"])
        self.assertFalse(payload["skip_queued_branch_builds"])
        self.assertIsNone(payload["skip_queued_branch_builds_filter"])
        self.assertFalse(payload["cancel_running_branch_builds"])
        self.assertIsNone(payload["cancel_running_branch_builds_filter"])
        self.assertEqual(payload["provider_settings"], base.QUARANTINE_PROVIDER)

    def test_persisted_core_rejects_wrong_github_provider_repo_before_patch(self) -> None:
        payload = pipeline()
        payload["provider"]["settings"]["repository"] = "attacker/other"
        with self.assertRaises(base.OperatorError):
            runtime.verify_persisted_core(
                json.dumps(payload),
                identity=identity(),
                expected_provider=base.QUARANTINE_PROVIDER,
                require_signature=True,
                require_webhook=True,
            )

    def test_create_normalizes_lifecycle_in_quarantine(self) -> None:
        resources = base.CreatedResources(
            cluster_uuid=CLUSTER_ID,
            upload_queue_uuid=QUEUE_IDS["hostpanel-upload"],
            ci_queue_uuid=QUEUE_IDS["hostpanel-ci"],
            qemu_queue_uuid=QUEUE_IDS["hostpanel-qemu"],
            pipeline_uuid=PIPELINE_ID,
            pipeline_slug="hostpanel",
        )
        calls: list[dict] = []

        def patch(provider):
            calls.append(provider)
            return json.dumps(pipeline(provider=provider, signed=False, webhook=False))

        with mock.patch.object(base, "preflight"), mock.patch.object(
            base, "assert_no_existing_control_plane"
        ), mock.patch.object(
            base, "run_bk", side_effect=[
                json.dumps({"id": CLUSTER_ID}),
                json.dumps({"id": QUEUE_IDS["hostpanel-upload"]}),
                json.dumps({"id": QUEUE_IDS["hostpanel-ci"]}),
                json.dumps({"id": QUEUE_IDS["hostpanel-qemu"]}),
                json.dumps({"id": PIPELINE_ID, "slug": "hostpanel"}),
            ]
        ), mock.patch.object(base, "verify_cluster", return_value=QUEUE_IDS), mock.patch.object(
            base, "assert_no_queue_agents"
        ), mock.patch.object(
            runtime, "fetch_pipeline_raw", return_value=json.dumps(pipeline(signed=False, webhook=False))
        ), mock.patch.object(
            runtime, "patch_pipeline_state", side_effect=patch
        ), mock.patch.object(
            runtime, "verify_full_patch_response", return_value=pipeline(signed=False, webhook=False)
        ), mock.patch.object(base, "assert_zero_builds"):
            actual = runtime.create_control_plane("example-org")
        self.assertEqual(actual, resources)
        self.assertEqual(calls, [base.QUARANTINE_PROVIDER])

    def test_activation_happy_path_orders_quarantine_webhook_active(self) -> None:
        patches: list[str] = []
        webhook_calls: list[list[str]] = []
        state = {"webhook": False}

        def patch(provider):
            mode = provider["trigger_mode"]
            patches.append(mode)
            return json.dumps(
                pipeline(
                    provider=provider,
                    signed=True,
                    webhook=state["webhook"],
                )
            )

        def run_bk(args):
            if args == ["api", "--method", "POST", "/pipelines/hostpanel/webhook"]:
                webhook_calls.append(args)
                state["webhook"] = True
                return "{}"
            raise AssertionError(args)

        with mock.patch.object(base, "preflight"), mock.patch.object(
            base, "verify_cluster", return_value=QUEUE_IDS
        ), mock.patch.object(base, "assert_no_queue_agents"), mock.patch.object(
            base, "assert_zero_builds"
        ), mock.patch.object(
            runtime, "fetch_pipeline_raw", side_effect=lambda: json.dumps(
                pipeline(
                    provider=base.ACTIVE_PROVIDER if patches and patches[-1] == "code" else base.QUARANTINE_PROVIDER,
                    signed=True,
                    webhook=state["webhook"],
                )
            )
        ), mock.patch.object(runtime, "patch_pipeline_state", side_effect=patch), mock.patch.object(
            runtime, "verify_full_patch_response", side_effect=lambda raw, **_: json.loads(raw)
        ), mock.patch.object(base, "run_bk", side_effect=run_bk):
            runtime.activate_control_plane("example-org", identity())
        self.assertEqual(patches, ["none", "none", "code"])
        self.assertEqual(len(webhook_calls), 1)

    def test_post_active_failure_rolls_back_to_quarantine(self) -> None:
        patches: list[str] = []
        state = {"webhook": True, "active_verified": False}

        def patch(provider):
            mode = provider["trigger_mode"]
            patches.append(mode)
            return json.dumps(pipeline(provider=provider, signed=True, webhook=True))

        build_checks = 0

        def zero_builds(_org):
            nonlocal build_checks
            build_checks += 1
            if patches and patches[-1] == "code":
                raise base.OperatorError("synthetic unexpected build after activation")

        def fetch():
            provider = base.ACTIVE_PROVIDER if patches and patches[-1] == "code" else base.QUARANTINE_PROVIDER
            return json.dumps(pipeline(provider=provider, signed=True, webhook=True))

        with mock.patch.object(base, "preflight"), mock.patch.object(
            base, "verify_cluster", return_value=QUEUE_IDS
        ), mock.patch.object(base, "assert_no_queue_agents"), mock.patch.object(
            base, "assert_zero_builds", side_effect=zero_builds
        ), mock.patch.object(runtime, "fetch_pipeline_raw", side_effect=fetch), mock.patch.object(
            runtime, "patch_pipeline_state", side_effect=patch
        ), mock.patch.object(
            runtime, "verify_full_patch_response", side_effect=lambda raw, **_: json.loads(raw)
        ):
            with self.assertRaises(base.OperatorError):
                runtime.activate_control_plane("example-org", identity())
        self.assertEqual(patches[-2:], ["code", "none"])
        self.assertGreaterEqual(build_checks, 3)

    def test_failed_quarantine_rollback_emits_manual_trigger_mode_instruction(self) -> None:
        patches: list[str] = []

        def patch(provider):
            mode = provider["trigger_mode"]
            patches.append(mode)
            if mode == "none" and "code" in patches:
                raise base.OperatorError("synthetic rollback failure")
            return json.dumps(pipeline(provider=provider, signed=True, webhook=True))

        def zero_builds(_org):
            if patches and patches[-1] == "code":
                raise base.OperatorError("synthetic active verification failure")

        with mock.patch.object(base, "preflight"), mock.patch.object(
            base, "verify_cluster", return_value=QUEUE_IDS
        ), mock.patch.object(base, "assert_no_queue_agents"), mock.patch.object(
            base, "assert_zero_builds", side_effect=zero_builds
        ), mock.patch.object(
            runtime, "fetch_pipeline_raw", return_value=json.dumps(pipeline(signed=True, webhook=True))
        ), mock.patch.object(runtime, "patch_pipeline_state", side_effect=patch), mock.patch.object(
            runtime, "verify_full_patch_response", side_effect=lambda raw, **_: json.loads(raw)
        ):
            with self.assertRaisesRegex(base.OperatorError, "trigger_mode=none manually"):
                runtime.activate_control_plane("example-org", identity())

    def test_pipeline_contract_and_codeowners_cover_runtime(self) -> None:
        self.assertIn(
            "tests.test_buildkite_control_plane_runtime",
            CONTRACT.read_text(encoding="utf-8"),
        )
        self.assertIn(
            "/tests/test_buildkite_control_plane_runtime.py @1-vps",
            CODEOWNERS.read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
