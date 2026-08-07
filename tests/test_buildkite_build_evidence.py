from __future__ import annotations

import base64
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

spec = importlib.util.spec_from_file_location(
    "hostpanel_build_evidence_test", VERIFIER
)
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
PR_NUMBER = 160


def signed_step(*, kid: str = "hostpanel-2026-08") -> dict:
    protected = base64.urlsafe_b64encode(
        json.dumps(
            {"alg": "EdDSA", "kid": kid}, separators=(",", ":")
        ).encode()
    ).decode().rstrip("=")
    return {
        "signature": {
            "algorithm": "EdDSA",
            "signed_fields": [
                "command",
                "env",
                "matrix",
                "plugins",
                "repository_url",
            ],
            "value": f"{protected}..b3BhcXVlLXNpZ25hdHVyZQ",
        }
    }


def pull_request_fixture(
    *,
    number: object = str(PR_NUMBER),
    base_branch: str = "main",
    repository: str = "https://github.com/1-vps/hostpanel.git",
) -> dict:
    return {
        "id": number,
        "base_branch": base_branch,
        "repository": repository,
    }


def build_fixture(mode: str = "pr") -> dict:
    return {
        "id": "44444444-4444-4444-4444-444444444444",
        "number": BUILD_NUMBER,
        "state": "passed",
        "blocked": False,
        "commit": COMMIT,
        "branch": BRANCH if mode == "pr" else "main",
        "source": "webhook",
        "pull_request": pull_request_fixture() if mode == "pr" else {},
        "rebuilt_from": None,
        "pipeline": {
            "id": PIPELINE_ID,
            "slug": "hostpanel",
            "repository": "git@github.com:1-vps/hostpanel.git",
        },
    }


def job_fixture(
    step_key: str,
    command: str,
    queue_id: str,
    index: int,
) -> dict:
    return {
        "id": str(uuid.UUID(int=index + 1)),
        "type": "script",
        "step_key": step_key,
        "step": signed_step(),
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
        for index, (
            step_key,
            (command, queue_id),
        ) in enumerate(expected.items())
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

    def verify_pr_build(self, payload: dict) -> str:
        return module.verify_build(
            payload,
            build_number=BUILD_NUMBER,
            expected_commit=COMMIT,
            expected_branch=BRANCH,
            pipeline_id=PIPELINE_ID,
            mode="pr",
            expected_pr_number=PR_NUMBER,
        )

    def verify_main_build(self, payload: dict) -> str:
        return module.verify_build(
            payload,
            build_number=BUILD_NUMBER,
            expected_commit=COMMIT,
            expected_branch="main",
            pipeline_id=PIPELINE_ID,
            mode="main",
            expected_pr_number=None,
        )

    def parser_args(
        self,
        *,
        mode: str = "pr",
        expected_branch: str | None = None,
        expected_pr_number: int | None = PR_NUMBER,
    ):
        args = [
            "--org",
            "example-org",
            "--build-number",
            str(BUILD_NUMBER),
            "--expected-commit",
            COMMIT,
            "--expected-branch",
            expected_branch or (BRANCH if mode == "pr" else "main"),
            "--mode",
            mode,
            "--pipeline-id",
            PIPELINE_ID,
            "--cluster-id",
            CLUSTER_ID,
            "--upload-queue-id",
            UPLOAD_QUEUE_ID,
            "--ci-queue-id",
            CI_QUEUE_ID,
            "--qemu-queue-id",
            QEMU_QUEUE_ID,
        ]
        if expected_pr_number is not None:
            args.extend(["--expected-pr-number", str(expected_pr_number)])
        return module.parser().parse_args(args)

    def test_pr_and_main_expected_script_job_counts_are_exact(self) -> None:
        self.assertEqual(
            len(
                module.expected_jobs(
                    mode="pr",
                    upload_queue_id=UPLOAD_QUEUE_ID,
                    ci_queue_id=CI_QUEUE_ID,
                    qemu_queue_id=QEMU_QUEUE_ID,
                )
            ),
            16,
        )
        self.assertEqual(
            len(
                module.expected_jobs(
                    mode="main",
                    upload_queue_id=UPLOAD_QUEUE_ID,
                    ci_queue_id=CI_QUEUE_ID,
                    qemu_queue_id=QEMU_QUEUE_ID,
                )
            ),
            17,
        )

    def test_reviewed_pr_job_inventory_passes(self) -> None:
        self.assertEqual(self.verify_jobs(jobs_fixture("pr")), 16)

    def test_reviewed_main_job_inventory_includes_qemu_and_passes(self) -> None:
        self.assertEqual(
            self.verify_jobs(jobs_fixture("main"), mode="main"),
            17,
        )
        keys = {
            job["step_key"]
            for job in jobs_fixture("main")["items"]
        }
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

    def test_unsigned_or_wrong_signature_metadata_is_rejected(self) -> None:
        mutations = (
            None,
            {
                "algorithm": "EdDSA",
                "signed_fields": ["command", "repository_url"],
                "value": signed_step()["signature"]["value"],
            },
            signed_step(kid="other-key")["signature"],
        )
        for signature in mutations:
            with self.subTest(signature=signature):
                payload = jobs_fixture()
                if signature is None:
                    payload["items"][0]["step"] = {}
                else:
                    payload["items"][0]["step"] = {
                        "signature": signature
                    }
                with self.assertRaises(module.EvidenceError):
                    self.verify_jobs(payload)

    def test_missing_required_build_or_job_field_is_rejected(self) -> None:
        build = build_fixture()
        del build["pull_request"]
        with self.assertRaisesRegex(
            module.EvidenceError,
            "missing required fields",
        ):
            self.verify_pr_build(build)
        jobs = jobs_fixture()
        del jobs["items"][0]["signal_reason"]
        with self.assertRaisesRegex(
            module.EvidenceError,
            "missing required fields",
        ):
            self.verify_jobs(jobs)

    def test_missing_duplicate_and_extra_attempts_are_rejected(self) -> None:
        missing = jobs_fixture()
        missing["items"].pop()
        with self.assertRaisesRegex(
            module.EvidenceError,
            "job count mismatch",
        ):
            self.verify_jobs(missing)

        duplicate = jobs_fixture()
        duplicate["items"][-1]["step_key"] = (
            duplicate["items"][0]["step_key"]
        )
        with self.assertRaises(module.EvidenceError):
            self.verify_jobs(duplicate)

        extra = jobs_fixture()
        extra["items"].append(copy.deepcopy(extra["items"][0]))
        extra["items"][-1]["id"] = (
            "66666666-6666-6666-6666-666666666666"
        )
        with self.assertRaisesRegex(
            module.EvidenceError,
            "job count mismatch",
        ):
            self.verify_jobs(extra)

    def test_jobs_pagination_and_shape_are_fail_closed(self) -> None:
        payload = jobs_fixture()
        payload["links"]["next"] = (
            "https://api.buildkite.com/v2/organizations/x/"
            ".../jobs?after=cursor"
        )
        with self.assertRaisesRegex(module.EvidenceError, "next cursor"):
            self.verify_jobs(payload)
        payload = jobs_fixture()
        payload["unexpected"] = True
        with self.assertRaisesRegex(module.EvidenceError, "top-level"):
            self.verify_jobs(payload)

    def test_build_metadata_is_bound_to_exact_webhook_commit_pipeline_branch_and_pr(
        self,
    ) -> None:
        build_id = self.verify_pr_build(build_fixture())
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
                    self.verify_pr_build(payload)

    def test_branch_webhook_same_sha_is_not_pr_evidence(self) -> None:
        payload = build_fixture()
        payload["pull_request"] = {}
        with self.assertRaisesRegex(
            module.EvidenceError,
            "branch webhook build is not acceptable",
        ):
            self.verify_pr_build(payload)

    def test_wrong_pr_id_base_or_repository_is_rejected(self) -> None:
        mutations = (
            {"id": "159"},
            {"base_branch": "release"},
            {"repository": "https://github.com/attacker/hostpanel.git"},
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                payload = build_fixture()
                payload["pull_request"].update(mutation)
                with self.assertRaises(module.EvidenceError):
                    self.verify_pr_build(payload)

    def test_pull_request_repository_identity_accepts_reviewed_url_forms(self) -> None:
        for repository in (
            "https://github.com/1-vps/hostpanel",
            "https://github.com/1-vps/hostpanel.git",
            "git://github.com/1-vps/hostpanel.git",
            "git@github.com:1-vps/hostpanel.git",
            "ssh://git@github.com/1-vps/hostpanel.git",
        ):
            with self.subTest(repository=repository):
                payload = build_fixture()
                payload["pull_request"]["repository"] = repository
                self.assertEqual(
                    self.verify_pr_build(payload),
                    build_fixture()["id"],
                )

    def test_pull_request_base_alias_is_supported_but_conflicts_fail_closed(
        self,
    ) -> None:
        payload = build_fixture()
        del payload["pull_request"]["base_branch"]
        payload["pull_request"]["base"] = "main"
        self.verify_pr_build(payload)

        payload = build_fixture()
        payload["pull_request"]["base"] = "release"
        with self.assertRaisesRegex(module.EvidenceError, "base branch"):
            self.verify_pr_build(payload)

    def test_main_evidence_requires_no_pull_request_metadata(self) -> None:
        self.assertEqual(
            self.verify_main_build(build_fixture("main")),
            build_fixture("main")["id"],
        )
        payload = build_fixture("main")
        payload["pull_request"] = pull_request_fixture()
        with self.assertRaisesRegex(
            module.EvidenceError,
            "must not contain pull request metadata",
        ):
            self.verify_main_build(payload)

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
                    self.verify_pr_build(payload)

    def test_any_artifact_record_is_rejected(self) -> None:
        module.verify_no_artifacts([])
        with self.assertRaisesRegex(module.EvidenceError, "artifact"):
            module.verify_no_artifacts([{"id": "artifact"}])

    def test_endpoints_request_retried_jobs_and_max_page(self) -> None:
        self.assertIn(
            "exclude_jobs=true",
            module.build_endpoint("example-org", BUILD_NUMBER),
        )
        jobs = module.jobs_endpoint("example-org", BUILD_NUMBER)
        self.assertIn("include_retried_jobs=true", jobs)
        self.assertIn("per_page=100", jobs)
        self.assertIn(
            "per_page=100",
            module.artifacts_endpoint("example-org", BUILD_NUMBER),
        )

    def test_preflight_requires_read_builds_and_read_artifacts(self) -> None:
        responses = [
            "{}",
            "{}",
            json.dumps(
                {"scopes": ["read_builds", "read_artifacts"]}
            ),
        ]
        with mock.patch.object(module, "run_bk", side_effect=responses):
            module.preflight("example-org")
        responses = [
            "{}",
            "{}",
            json.dumps({"scopes": ["read_builds"]}),
        ]
        with mock.patch.object(module, "run_bk", side_effect=responses):
            with self.assertRaisesRegex(
                module.EvidenceError,
                "read_artifacts",
            ):
                module.preflight("example-org")

    def test_argument_validation_binds_pr_number_to_mode(self) -> None:
        args = self.parser_args()
        module.validate_args(args)
        self.assertEqual(args.expected_pr_number, PR_NUMBER)

        args = self.parser_args(expected_pr_number=None)
        with self.assertRaisesRegex(
            module.EvidenceError,
            "requires a positive --expected-pr-number",
        ):
            module.validate_args(args)

        args = self.parser_args(
            mode="main",
            expected_pr_number=None,
        )
        module.validate_args(args)

        args = self.parser_args(
            mode="main",
            expected_pr_number=PR_NUMBER,
        )
        with self.assertRaisesRegex(
            module.EvidenceError,
            "must not specify --expected-pr-number",
        ):
            module.validate_args(args)

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

    def test_pipeline_contract_and_codeowners_cover_evidence_tests(
        self,
    ) -> None:
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
