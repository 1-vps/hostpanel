from __future__ import annotations

import base64
import copy
import importlib.util
import json
import pathlib
import sys
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
VERIFIER = ROOT / ".buildkite/operator/verify-pipeline-state.py"
CONTRACT = ROOT / ".buildkite/scripts/run-pipeline-contract.sh"
CODEOWNERS = ROOT / ".github/CODEOWNERS"

spec = importlib.util.spec_from_file_location("hostpanel_pipeline_state", VERIFIER)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

CLUSTER_ID = "01234567-89ab-cdef-0123-456789abcdef"


def jws_value() -> str:
    header = json.dumps(
        {"alg": "EdDSA", "kid": "hostpanel-2026-08"}, separators=(",", ":")
    ).encode()
    encoded = base64.urlsafe_b64encode(header).decode().rstrip("=")
    return f"{encoded}..opaque-signature"


def fixture(*, signed: bool = True, webhook: bool = True) -> dict:
    step = {
        "label": ":pipeline: Upload reviewed pipeline",
        "key": "upload-reviewed-pipeline",
        "command": "/usr/local/libexec/hostpanel-upload-pipeline",
        "checkout": {"skip": True},
        "agents": {"queue": "hostpanel-upload"},
    }
    if signed:
        step["signature"] = {
            "algorithm": "EdDSA",
            "signed_fields": [
                "command",
                "env",
                "matrix",
                "plugins",
                "repository_url",
            ],
            "value": jws_value(),
        }
    return {
        "id": "11111111-2222-3333-4444-555555555555",
        "name": "HostPanel",
        "slug": "hostpanel",
        "repository": "git@github.com:1-vps/hostpanel.git",
        "cluster_id": CLUSTER_ID,
        "visibility": "private",
        "env": {},
        "branch_configuration": "main",
        "default_branch": "main",
        "allow_rebuilds": False,
        "archived_at": None,
        "pipeline_template_uuid": None,
        "skip_queued_branch_builds": False,
        "skip_queued_branch_builds_filter": None,
        "cancel_running_branch_builds": False,
        "cancel_running_branch_builds_filter": None,
        "provider": {
            "id": "github",
            "webhook_url": (
                "https://webhook.buildkite.com/deliver/abcDEF0123_-"
                if webhook
                else ""
            ),
            "settings": {
                "repository": "1-vps/hostpanel",
                "trigger_mode": "none",
            },
        },
        "configuration": yaml.safe_dump({"steps": [step]}, sort_keys=False),
    }


class BuildkitePipelineStateTests(unittest.TestCase):
    def verify(self, payload: dict, *, signed: bool = True, webhook: bool = True) -> None:
        module.verify_pipeline_state(
            payload,
            expected_cluster_id=CLUSTER_ID,
            require_signature=signed,
            require_webhook=webhook,
        )

    def test_reviewed_signed_state_passes(self) -> None:
        self.verify(fixture())

    def test_unsigned_quarantine_state_passes_without_webhook_requirement(self) -> None:
        self.verify(fixture(signed=False, webhook=False), signed=False, webhook=False)

    def test_provider_repository_is_bound_separately_from_checkout_repository(self) -> None:
        payload = fixture()
        payload["provider"]["settings"]["repository"] = "attacker/other"
        with self.assertRaises(module.VerificationError):
            self.verify(payload)

    def test_pipeline_template_is_forbidden_for_signed_pipeline(self) -> None:
        payload = fixture()
        payload["pipeline_template_uuid"] = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        with self.assertRaises(module.VerificationError):
            self.verify(payload)

    def test_archived_pipeline_is_rejected(self) -> None:
        payload = fixture()
        payload["archived_at"] = "2026-08-07T20:00:00Z"
        with self.assertRaises(module.VerificationError):
            self.verify(payload)

    def test_skip_and_cancel_branch_build_state_is_pinned(self) -> None:
        mutations = (
            ("skip_queued_branch_builds", True),
            ("skip_queued_branch_builds_filter", "main"),
            ("cancel_running_branch_builds", True),
            ("cancel_running_branch_builds_filter", "main"),
        )
        for key, value in mutations:
            with self.subTest(key=key):
                payload = fixture()
                payload[key] = value
                with self.assertRaises(module.VerificationError):
                    self.verify(payload)

    def test_webhook_url_is_exact_buildkite_https_origin_and_one_deliver_segment(self) -> None:
        bad = (
            "http://webhook.buildkite.com/deliver/abc",
            "https://evil.example/deliver/abc",
            "https://user@webhook.buildkite.com/deliver/abc",
            "https://webhook.buildkite.com:443/deliver/abc",
            "https://webhook.buildkite.com/deliver/abc/extra",
            "https://webhook.buildkite.com/deliver/abc?x=1",
            "https://webhook.buildkite.com/deliver/abc#frag",
            "https://webhook.buildkite.com/deliver/",
        )
        for value in bad:
            with self.subTest(value=value):
                payload = fixture()
                payload["provider"]["webhook_url"] = value
                with self.assertRaises(module.VerificationError):
                    self.verify(payload)

    def test_signed_fields_match_complete_reviewed_surface_exactly(self) -> None:
        document = yaml.safe_load(fixture()["configuration"])
        original = document["steps"][0]["signature"]["signed_fields"]
        variants = (
            [item for item in original if item != "env"],
            [*original, "extra"],
            [*original, "command"],
        )
        for fields in variants:
            with self.subTest(fields=fields):
                payload = fixture()
                parsed = yaml.safe_load(payload["configuration"])
                parsed["steps"][0]["signature"]["signed_fields"] = fields
                payload["configuration"] = yaml.safe_dump(parsed, sort_keys=False)
                with self.assertRaises(module.VerificationError):
                    self.verify(payload)

    def test_jws_header_algorithm_and_key_id_are_pinned(self) -> None:
        for header in (
            {"alg": "ES512", "kid": "hostpanel-2026-08"},
            {"alg": "EdDSA", "kid": "other-key"},
        ):
            payload = fixture()
            parsed = yaml.safe_load(payload["configuration"])
            encoded = base64.urlsafe_b64encode(
                json.dumps(header, separators=(",", ":")).encode()
            ).decode().rstrip("=")
            parsed["steps"][0]["signature"]["value"] = f"{encoded}..opaque"
            payload["configuration"] = yaml.safe_dump(parsed, sort_keys=False)
            with self.assertRaises(module.VerificationError):
                self.verify(payload)

    def test_cluster_uuid_must_be_canonical_and_exact(self) -> None:
        payload = fixture()
        payload["cluster_id"] = CLUSTER_ID.upper()
        with self.assertRaises(module.VerificationError):
            self.verify(payload)
        payload = fixture()
        with self.assertRaises(module.VerificationError):
            module.verify_pipeline_state(
                payload,
                expected_cluster_id="11111111-2222-3333-4444-555555555555",
                require_signature=True,
                require_webhook=True,
            )

    def test_pipeline_lifecycle_baseline_is_explicit_in_fixture(self) -> None:
        payload = fixture()
        self.assertIsNone(payload["pipeline_template_uuid"])
        self.assertIsNone(payload["archived_at"])
        self.assertFalse(payload["skip_queued_branch_builds"])
        self.assertFalse(payload["cancel_running_branch_builds"])
        self.assertFalse(payload["allow_rebuilds"])

    def test_pipeline_contract_and_codeowners_cover_verifier(self) -> None:
        self.assertIn(
            "tests.test_buildkite_pipeline_state",
            CONTRACT.read_text(encoding="utf-8"),
        )
        self.assertIn(
            "/tests/test_buildkite_pipeline_state.py @1-vps",
            CODEOWNERS.read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
