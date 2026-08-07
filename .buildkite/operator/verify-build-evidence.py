#!/usr/bin/env python3
from __future__ import annotations

import sys

if __name__ == "__main__" and not sys.flags.isolated:
    print(
        "HostPanel Buildkite evidence verifier requires isolated Python; "
        "use .buildkite/operator/verify-build-evidence.sh or python3 -I.",
        file=sys.stderr,
    )
    raise SystemExit(2)

import argparse
import json
import os
import re
import shutil
import subprocess
import uuid

SAFE_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
PIPELINE_SLUG = "hostpanel"
REPOSITORY = "git@github.com:1-vps/hostpanel.git"
REQUIRED_SCOPES = frozenset({"read_builds", "read_artifacts"})
ORG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
MAX_JOBS = 100

REQUIRED_BUILD_FIELDS = frozenset(
    {
        "id",
        "number",
        "state",
        "blocked",
        "commit",
        "branch",
        "source",
        "rebuilt_from",
        "pipeline",
    }
)
REQUIRED_JOB_FIELDS = frozenset(
    {
        "id",
        "type",
        "step_key",
        "command",
        "state",
        "soft_failed",
        "exit_status",
        "signal",
        "signal_reason",
        "broken_reason",
        "artifact_paths",
        "retried",
        "retried_in_job_id",
        "retries_count",
        "retry_source",
        "retry_type",
        "parallel_group_index",
        "parallel_group_total",
        "matrix",
        "cluster_id",
        "cluster_queue_id",
        "started_at",
        "finished_at",
    }
)

UPLOAD_STEP = (
    "upload-reviewed-pipeline",
    "/usr/local/libexec/hostpanel-upload-pipeline",
)
CI_COMMANDS = {
    "pipeline-contract": ".buildkite/scripts/run-pipeline-contract.sh",
    "repository-regressions": ".buildkite/scripts/run-check.sh",
    "installer-static": ".buildkite/scripts/run-check.sh",
    "release-metadata": ".buildkite/scripts/run-check.sh",
    "production-validator": ".buildkite/scripts/run-check.sh",
    "qemu-contracts": ".buildkite/scripts/run-check.sh",
    "os-ubuntu-2204": ".buildkite/scripts/run-check.sh",
    "os-ubuntu-2404": ".buildkite/scripts/run-check.sh",
    "os-ubuntu-2604": ".buildkite/scripts/run-check.sh",
    "os-debian-12": ".buildkite/scripts/run-check.sh",
    "os-debian-13": ".buildkite/scripts/run-check.sh",
    "os-rocky-9": ".buildkite/scripts/run-check.sh",
    "os-rocky-10": ".buildkite/scripts/run-check.sh",
    "os-almalinux-9": ".buildkite/scripts/run-check.sh",
    "os-almalinux-10": ".buildkite/scripts/run-check.sh",
}
QEMU_STEP = ("qemu-vm-acceptance", ".buildkite/scripts/run-qemu.sh")


class EvidenceError(RuntimeError):
    pass


def canonical_uuid(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise EvidenceError(f"{label} is missing")
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise EvidenceError(f"{label} is not a UUID") from exc
    canonical = str(parsed)
    if value != canonical:
        raise EvidenceError(f"{label} is not canonical lowercase UUID form")
    return canonical


def require_fields(payload: dict, required: frozenset[str], label: str) -> None:
    missing = sorted(required.difference(payload))
    if missing:
        raise EvidenceError(f"{label} is missing required fields: " + ", ".join(missing))


def safe_environment() -> dict[str, str]:
    allowed = {"PATH": SAFE_PATH}
    for key in (
        "HOME",
        "USER",
        "LOGNAME",
        "LANG",
        "LC_ALL",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_RUNTIME_DIR",
    ):
        value = os.environ.get(key)
        if value:
            allowed[key] = value
    return allowed


def bk_binary() -> str:
    path = shutil.which("bk", path=SAFE_PATH)
    if not path:
        raise EvidenceError("bk CLI is unavailable in the reviewed system PATH")
    return path


def run_bk(args: list[str]) -> str:
    result = subprocess.run(
        [bk_binary(), *args],
        env=safe_environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip()
        if len(detail) > 400:
            detail = detail[:400] + "..."
        suffix = f": {detail}" if detail else ""
        raise EvidenceError(f"bk command failed with status {result.returncode}{suffix}")
    return result.stdout


def parse_json(raw: str, label: str):
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise EvidenceError(f"{label} did not return valid JSON") from exc


def preflight(org: str) -> None:
    run_bk(["auth", "switch", org])
    run_bk(["auth", "status", "-o", "json"])
    payload = parse_json(run_bk(["api", "/access-token"]), "bk api /access-token")
    if not isinstance(payload, dict) or not isinstance(payload.get("scopes"), list):
        raise EvidenceError("Buildkite access-token response does not contain scopes")
    scopes = payload["scopes"]
    if not all(isinstance(item, str) for item in scopes):
        raise EvidenceError("Buildkite access-token scopes contain a non-string value")
    missing = sorted(REQUIRED_SCOPES.difference(scopes))
    if missing:
        raise EvidenceError("Buildkite credential is missing required scopes: " + ", ".join(missing))


def build_endpoint(org: str, build_number: int) -> str:
    return (
        f"/organizations/{org}/pipelines/{PIPELINE_SLUG}/builds/{build_number}"
        "?exclude_jobs=true"
    )


def jobs_endpoint(org: str, build_number: int) -> str:
    return (
        f"/organizations/{org}/pipelines/{PIPELINE_SLUG}/builds/{build_number}/jobs"
        f"?include_retried_jobs=true&per_page={MAX_JOBS}"
    )


def artifacts_endpoint(org: str, build_number: int) -> str:
    return (
        f"/organizations/{org}/pipelines/{PIPELINE_SLUG}/builds/{build_number}/artifacts"
        "?per_page=100"
    )


def verify_build(
    payload: object,
    *,
    build_number: int,
    expected_commit: str,
    expected_branch: str,
    pipeline_id: str,
) -> str:
    if not isinstance(payload, dict):
        raise EvidenceError("build response is not a JSON object")
    require_fields(payload, REQUIRED_BUILD_FIELDS, "build response")
    build_id = canonical_uuid(payload["id"], "build id")
    if payload["number"] != build_number:
        raise EvidenceError("build number mismatch")
    if payload["state"] != "passed":
        raise EvidenceError("build state is not passed")
    if payload["blocked"] is not False:
        raise EvidenceError("build is blocked")
    if payload["commit"] != expected_commit:
        raise EvidenceError("build commit does not match the exact expected SHA")
    if payload["branch"] != expected_branch:
        raise EvidenceError("build branch does not match the expected branch")
    if payload["source"] != "webhook":
        raise EvidenceError("merge evidence must come from a webhook build")
    if payload["rebuilt_from"] is not None:
        raise EvidenceError("rebuilt builds are not accepted as merge evidence")
    pipeline = payload["pipeline"]
    if not isinstance(pipeline, dict):
        raise EvidenceError("build pipeline object is unavailable")
    for field in ("id", "slug", "repository"):
        if field not in pipeline:
            raise EvidenceError(f"build pipeline object is missing required field: {field}")
    if canonical_uuid(pipeline["id"], "build pipeline id") != pipeline_id:
        raise EvidenceError("build pipeline id mismatch")
    if pipeline["slug"] != PIPELINE_SLUG:
        raise EvidenceError("build pipeline slug mismatch")
    if pipeline["repository"] != REPOSITORY:
        raise EvidenceError("build pipeline repository mismatch")
    return build_id


def expected_jobs(
    *,
    mode: str,
    upload_queue_id: str,
    ci_queue_id: str,
    qemu_queue_id: str,
) -> dict[str, tuple[str, str]]:
    jobs = {UPLOAD_STEP[0]: (UPLOAD_STEP[1], upload_queue_id)}
    jobs.update({key: (command, ci_queue_id) for key, command in CI_COMMANDS.items()})
    if mode == "main":
        jobs[QEMU_STEP[0]] = (QEMU_STEP[1], qemu_queue_id)
    return jobs


def require_none(job: dict, field: str, step_key: str) -> None:
    if job.get(field) is not None:
        raise EvidenceError(f"{step_key}: {field} must be null")


def verify_job(
    job: object,
    *,
    step_key: str,
    expected_command: str,
    cluster_id: str,
    queue_id: str,
) -> str:
    if not isinstance(job, dict):
        raise EvidenceError(f"{step_key}: job is not a JSON object")
    require_fields(job, REQUIRED_JOB_FIELDS, f"{step_key} job")
    job_id = canonical_uuid(job["id"], f"{step_key} job id")
    if job["type"] != "script":
        raise EvidenceError(f"{step_key}: job type is not script")
    if job["step_key"] != step_key:
        raise EvidenceError(f"{step_key}: step key mismatch")
    if job["command"] != expected_command:
        raise EvidenceError(f"{step_key}: command mismatch")
    if job["state"] != "passed":
        raise EvidenceError(f"{step_key}: state is not passed")
    if job["soft_failed"] is not False:
        raise EvidenceError(f"{step_key}: soft_failed must be false")
    exit_status = job["exit_status"]
    if isinstance(exit_status, bool) or not isinstance(exit_status, int) or exit_status != 0:
        raise EvidenceError(f"{step_key}: exit_status must be integer zero")
    for field in (
        "signal",
        "signal_reason",
        "broken_reason",
        "retried_in_job_id",
        "retry_source",
        "retry_type",
        "parallel_group_index",
        "parallel_group_total",
        "matrix",
    ):
        require_none(job, field, step_key)
    for optional_failure_field in ("promised_exit_status", "promised_exit_status_at"):
        if optional_failure_field in job and job[optional_failure_field] is not None:
            raise EvidenceError(f"{step_key}: {optional_failure_field} must be absent or null")
    if "retried_by" in job and job["retried_by"] is not None:
        raise EvidenceError(f"{step_key}: retried_by must be absent or null")
    if job["retried"] is not False:
        raise EvidenceError(f"{step_key}: retried must be false")
    if job["retries_count"] not in (None, 0):
        raise EvidenceError(f"{step_key}: retries_count proves retry history")
    if job["artifact_paths"] not in (None, ""):
        raise EvidenceError(f"{step_key}: artifact_paths must be empty")
    if canonical_uuid(job["cluster_id"], f"{step_key} cluster id") != cluster_id:
        raise EvidenceError(f"{step_key}: cluster id mismatch")
    if canonical_uuid(job["cluster_queue_id"], f"{step_key} queue id") != queue_id:
        raise EvidenceError(f"{step_key}: queue id mismatch")
    if not isinstance(job["started_at"], str) or not job["started_at"]:
        raise EvidenceError(f"{step_key}: started_at is missing")
    if not isinstance(job["finished_at"], str) or not job["finished_at"]:
        raise EvidenceError(f"{step_key}: finished_at is missing")
    return job_id


def verify_jobs_page(
    payload: object,
    *,
    mode: str,
    cluster_id: str,
    upload_queue_id: str,
    ci_queue_id: str,
    qemu_queue_id: str,
) -> int:
    if not isinstance(payload, dict):
        raise EvidenceError("jobs response is not a JSON object")
    if set(payload).difference({"items", "links"}):
        # Extra top-level metadata is not security-sensitive, but fail closed so
        # an API shape change is explicitly reviewed before merge evidence is trusted.
        raise EvidenceError("jobs response contains unreviewed top-level fields")
    items = payload.get("items")
    links = payload.get("links")
    if not isinstance(items, list) or not isinstance(links, dict):
        raise EvidenceError("jobs response is missing items/links")
    if links.get("next") not in (None, ""):
        raise EvidenceError(
            "jobs response has a next cursor; exact HostPanel job inventory must fit on one 100-item page"
        )
    expected = expected_jobs(
        mode=mode,
        upload_queue_id=upload_queue_id,
        ci_queue_id=ci_queue_id,
        qemu_queue_id=qemu_queue_id,
    )
    if len(items) != len(expected):
        raise EvidenceError(
            f"job count mismatch: expected {len(expected)}, received {len(items)}"
        )
    seen_steps: set[str] = set()
    seen_job_ids: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise EvidenceError("jobs inventory contains a non-object item")
        step_key = item.get("step_key")
        if not isinstance(step_key, str) or step_key not in expected:
            raise EvidenceError(f"unexpected command job step key: {step_key!r}")
        if step_key in seen_steps:
            raise EvidenceError(f"duplicate/retried job attempt for step key: {step_key}")
        command, queue_id = expected[step_key]
        job_id = verify_job(
            item,
            step_key=step_key,
            expected_command=command,
            cluster_id=cluster_id,
            queue_id=queue_id,
        )
        if job_id in seen_job_ids:
            raise EvidenceError("duplicate job id in jobs inventory")
        seen_steps.add(step_key)
        seen_job_ids.add(job_id)
    if seen_steps != set(expected):
        missing = sorted(set(expected).difference(seen_steps))
        raise EvidenceError("required job steps are missing: " + ", ".join(missing))
    return len(items)


def verify_no_artifacts(payload: object) -> None:
    if not isinstance(payload, list):
        raise EvidenceError("artifact inventory is not a JSON list")
    if payload:
        raise EvidenceError(
            f"build contains {len(payload)} artifact record(s); HostPanel merge evidence requires zero"
        )


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(
        description="Verify exact HostPanel Buildkite job evidence before merge."
    )
    top.add_argument("--org", required=True)
    top.add_argument("--build-number", required=True, type=int)
    top.add_argument("--expected-commit", required=True)
    top.add_argument("--expected-branch", required=True)
    top.add_argument("--mode", required=True, choices=("pr", "main"))
    top.add_argument("--pipeline-id", required=True)
    top.add_argument("--cluster-id", required=True)
    top.add_argument("--upload-queue-id", required=True)
    top.add_argument("--ci-queue-id", required=True)
    top.add_argument("--qemu-queue-id", required=True)
    return top


def validate_args(args: argparse.Namespace) -> None:
    if ORG_RE.fullmatch(args.org) is None:
        raise EvidenceError("invalid --org")
    if args.build_number <= 0:
        raise EvidenceError("--build-number must be positive")
    if SHA_RE.fullmatch(args.expected_commit) is None:
        raise EvidenceError("--expected-commit must be one full lowercase 40-character SHA")
    if not args.expected_branch or len(args.expected_branch) > 255 or any(
        ch in args.expected_branch for ch in "\r\n\x00"
    ):
        raise EvidenceError("--expected-branch is malformed")
    if args.mode == "main" and args.expected_branch != "main":
        raise EvidenceError("main evidence must target branch main")
    for name in (
        "pipeline_id",
        "cluster_id",
        "upload_queue_id",
        "ci_queue_id",
        "qemu_queue_id",
    ):
        setattr(args, name, canonical_uuid(getattr(args, name), f"--{name.replace('_', '-')}"))


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        validate_args(args)
        preflight(args.org)
        build = parse_json(
            run_bk(["api", build_endpoint(args.org, args.build_number)]),
            "Buildkite build",
        )
        build_id = verify_build(
            build,
            build_number=args.build_number,
            expected_commit=args.expected_commit,
            expected_branch=args.expected_branch,
            pipeline_id=args.pipeline_id,
        )
        jobs = parse_json(
            run_bk(["api", jobs_endpoint(args.org, args.build_number)]),
            "Buildkite jobs",
        )
        job_count = verify_jobs_page(
            jobs,
            mode=args.mode,
            cluster_id=args.cluster_id,
            upload_queue_id=args.upload_queue_id,
            ci_queue_id=args.ci_queue_id,
            qemu_queue_id=args.qemu_queue_id,
        )
        artifacts = parse_json(
            run_bk(["api", artifacts_endpoint(args.org, args.build_number)]),
            "Buildkite artifacts",
        )
        verify_no_artifacts(artifacts)
        print("HostPanel exact Buildkite merge evidence verified.")
        print(f"build_id={build_id}")
        print(f"build_number={args.build_number}")
        print(f"commit={args.expected_commit}")
        print(f"mode={args.mode}")
        print(f"verified_script_jobs={job_count}")
        print("artifacts=0")
        return 0
    except EvidenceError as exc:
        print(f"HostPanel Buildkite evidence rejected: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
