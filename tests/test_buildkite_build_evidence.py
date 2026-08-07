from __future__ import annotations

import copy
import importlib.util
import json
import pathlib
import subprocess
import sys
import unittest
import uuid
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
VERIFIER = ROOT / ".buildkite/operator/verify-build-evidence.py"
WRAPPER = ROOT / ".buildkite/operator/verify-build-evidence.sh"
CONTRACT = ROOT / ".buildkite/scripts/run-pipeline-contract.sh"
CODEOWNERS = ROOT / ".github/CODEOWNERS"

spec = importlib.util.spec_from_file_location("hostpanel_build_evidence_test", VERIFIER)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

PIPELINE_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
CLUSTER_ID = "01234567-89ab-cdef-0123-456789abcdef"
UPLOAD_QUEUE_ID = "11111111-1111-1111-1111-111111111111"
CI_QUEUE_ID = "22222222-2222-2222-2222-222222222222"
QEMU_QUEUE_ID = "33333333-3333-3333-3333-333333333333"
COMMIT = "a" * 40
BRANCH = "agent/release-consistency-foundation"
BUILD_NUMBER = 42


def build_fixture() -> dict:
    return {
        "id": "44444444-4444-4444-4444-444444444444",
        "number": BUILD_NUMBER,
        "state": "passed",
        "blocked": False,
        "commit": COMMIT,
        "branch": BRANCH,
        "source": "webhook",
        "rebuilt_from": None,
        "pipeline": {
            "id": PIPELINE_ID,
            "slug": "hostpanel",
            "repository": "git@github.com:1-vps/hostpanel.git",
        },
    }


def job_fixture(step_key: str, command: str, queue_id: str, index: int) -> dict:
    return {
        "id": str(uuid.UUID(int=index + 1)),
        "type": "script",
        "step_key": step_key,
        "command": command,
        "state": "passed",
        "soft_failed": False,
        "exit_status": 0,
        "signal": None,
        "signal_reason": None,
        "broken_reason": None,
        "artifact_paths": None,
        "retried": False,
        "retried_in_job_id": None,
        "retries_count": None,
        "retry_source": None,
        "retry_type": None,
        "retried_by": None,
        "parallel_group_index": None,
        "parallel_group_total": None,
        "matrix": None,
        "cluster_id": CLUSTER_ID,
        "cluster_queue_id": queue_id,
        "started_at": "2026-08-07T20:00:00Z",
        "finished_at": "2026-08-07T20:01:00Z",
    }


def jobs_fixture(mode: str = "pr") -> dict:
    expected = module.expected_jobs(
        mode=mode,
        upload_queue_id=UPLOAD_QUEUE_ID,
        ci_queue_id=CI_QUEUE_ID,
        qemu_queue_id=QEMU_QUEUE_ID,
    )
    items = [
        job_fixture(step_key, command, queue_id, index)
        for index, (step_key, (command, queue_id)) in enumerate(expected.items())
    ]
    return {"items": items, "links": {"next": None}}


class BuildkiteBuildEvidenceTests(unittest.TestCase):
    def verify_jobs(self, payload: dict, mode: str = "pr") -> int:
        return module.verify_jobs_page(
            payload,
            mode=mode,
            cluster_id=CLUSTER_ID,
            upload_queue_id=UPLOAD_QUEUE_ID,
            ci_queue_id=CI_QUEUE_ID,
            qemu_queue_id=QEMU_QUEUE_ID,
        )

    def test_pr_and_main_expected_script_job_counts_are_exact(self) -> None:
        self.assertEqual(len(module.expected_jobs(
            mode="pr",
            upload_queue_id=UPLOAD_QUEUE_ID,
            ci_queue_id=CI_QUEUE_ID,
            qemu_queue_id=QEMU_QUEUE_ID,
        )), 16)
        self.assertEqual(len(module.expected_jobs(
            mode="main",
            upload_queue_id=UPLOAD_QUEUE_ID,
            ci_queue_id=CI_QUEUE_ID,
            qemu_queue_id=QEMU_QUEUE_ID,
        )), 17)

    def test_reviewed_pr_job_inventory_passes(self) -> None:
        self.assertEqual(self.verify_jobs(jobs_fixture("pr")), 16)

    def test_reviewed_main_job_inventory_includes_qemu_and_passes(self) -> None:
        self.assertEqual(self.verify_jobs(jobs_fixture("main"), mode="main"), 17)
        keys = {job["step_key"] for job in jobs_fixture("main")["items"]}
        self.assertIn("qemu-vm-acceptance", keys)

    def test_each_execution_integrity_mutation_is_rejected(self) -> None:
        mutations = {
            "state": "failed",
            "soft_failed": True,
            "exit_status": 1,
            "signal": "SIGTERM",
            "signal_reason": "agent_stop",
            "broken_reason": "conditional_failed",
            "artifact_paths": "private/**/*",
            "retried": True,
            "retried_in_job_id": "55555555-5555-5555-5555-555555555555",
            "retries_count": 1,
            "retry_source": "manual",
            "retry_type": "manual",
            "retried_by": {"id": "x"},
            "parallel_group_index": 0,
            "parallel_group_total": 2,
            "matrix": {"os": "other"},
        }
        for field, value in mutations.items():
            with self.subTest(field=field):
                payload = jobs_fixture()
                payload["items"][0][field] = value
                with self.assertRaises(module.EvidenceError):
                    self.verify_jobs(payload)

    def test_command_cluster_and_queue_mutations_are_rejected(self) -> None:
        for field, value in (
            ("command", "echo pwned"),
            ("cluster_id", "55555555-5555-5555-5555-555555555555"),
            ("cluster_queue_id", "55555555-5555-5555-5555-555555555555"),
            ("type", "trigger"),
        ):
            with self.subTest(field=field):
                payload = jobs_fixture()
                payload["items"][0][field] = value
                with self.assertRaises(module.EvidenceError):
                    self.verify_jobs(payload)

    def test_missing_duplicate_and_extra_attempts_are_rejected(self) -> None:
        missing = jobs_fixture()
        missing["items"].pop()
        with self.assertRaisesRegex(module.EvidenceError, "job count mismatch"):
            self.verify_jobs(missing)

        duplicate = jobs_fixture()
        duplicate["items"][-1]["step_key"] = duplicate["items"][0]["step_key"]
        with self.assertRaises(module.EvidenceError):
            self.verify_jobs(duplicate)

        extra = jobs_fixture()
        extra["items"].append(copy.deepcopy(extra["items"][0]))
        extra["items"][-1]["id"] = "66666666-6666-6666-6666-666666666666"
        with self.assertRaisesRegex(module.EvidenceError, "job count mismatch"):
            self.verify_jobs(extra)

    def test_jobs_pagination_is_fail_closed(self) -> None:
        payload = jobs_fixture()
        payload["links"]["next"] = "https://api.buildkite.com/v2/organizations/x/.../jobs?after=cursor"
        with self.assertRaisesRegex(module.EvidenceError, "next cursor"):
            self.verify_jobs(payload)

    def test_build_metadata_is_bound_to_exact_webhook_commit_pipeline_and_branch(self) -> None:
        build_id = module.verify_build(
            build_fixture(),
            build_number=BUILD_NUMBER,
            expected_commit=COMMIT,
            expected_branch=BRANCH,
            pipeline_id=PIPELINE_ID,
        )
        self.assertEqual(build_id, build_fixture()["id"])
        mutations = {
            "state": "failed",
            "blocked": True,
            "commit": "b" * 40,
            "branch": "other",
            "source": "api",
            "rebuilt_from": {"id": "old"},
        }
        for field, value in mutations.items():
            with self.subTest(field=field):
                payload = build_fixture()
                payload[field] = value
                with self.assertRaises(module.EvidenceError):
                    module.verify_build(
                        payload,
                        build_number=BUILD_NUMBER,
                        expected_commit=COMMIT,
                        expected_branch=BRANCH,
                        pipeline_id=PIPELINE_ID,
                    )

    def test_build_pipeline_identity_mutations_are_rejected(self) -> None:
        for field, value in (
            ("id", "55555555-5555-5555-5555-555555555555"),
            ("slug", "other"),
            ("repository", "git@github.com:attacker/other.git"),
        ):
            with self.subTest(field=field):
                payload = build_fixture()
                payload["pipeline"][field] = value
                with self.assertRaises(module.EvidenceError):
                    module.verify_build(
                        payload,
                        build_number=BUILD_NUMBER,
                        expected_commit=COMMIT,
                        expected_branch=BRANCH,
                        pipeline_id=PIPELINE_ID,
                    )

    def test_any_artifact_record_is_rejected(self) -> None:
        module.verify_no_artifacts([])
        with self.assertRaisesRegex(module.EvidenceError, "artifact"):
            module.verify_no_artifacts([{"id": "artifact"}])

    def test_endpoints_request_retried_jobs_and_max_page(self) -> None:
        self.assertIn("exclude_jobs=true", module.build_endpoint("example-org", BUILD_NUMBER))
        jobs = module.jobs_endpoint("example-org", BUILD_NUMBER)
        self.assertIn("include_retried_jobs=true", jobs)
        self.assertIn("per_page=100", jobs)
        self.assertIn("per_page=100", module.artifacts_endpoint("example-org", BUILD_NUMBER))

    def test_preflight_requires_read_builds_and_read_artifacts(self) -> None:
        responses = ["{}", "{}", json.dumps({"scopes": ["read_builds", "read_artifacts"]})]
        with mock.patch.object(module, "run_bk", side_effect=responses):
            module.preflight("example-org")
        responses = ["{}", "{}", json.dumps({"scopes": ["read_builds"]})]
        with mock.patch.object(module, "run_bk", side_effect=responses):
            with self.assertRaisesRegex(module.EvidenceError, "read_artifacts"):
                module.preflight("example-org")

    def test_direct_nonisolated_execution_is_rejected(self) -> None:
        result = subprocess.run(
            [sys.executable, str(VERIFIER), "--help"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("requires isolated Python", result.stderr)

    def test_wrapper_uses_isolated_python(self) -> None:
        text = WRAPPER.read_text(encoding="utf-8")
        self.assertIn('exec python3 -I "$verifier" "$@"', text)

    def test_pipeline_contract_and_codeowners_cover_evidence_tests(self) -> None:
        self.assertIn(
            "tests.test_buildkite_build_evidence",
            CONTRACT.read_text(encoding="utf-8"),
        )
        self.assertIn(
            "/tests/test_buildkite_build_evidence.py @1-vps",
            CODEOWNERS.read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
