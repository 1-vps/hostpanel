#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import pathlib
import re
import shutil
import stat
import subprocess
import sys
import uuid
from dataclasses import dataclass

SAFE_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
REQUIRED_SCOPES = frozenset(
    {
        "read_clusters",
        "write_clusters",
        "read_pipelines",
        "write_pipelines",
        "read_builds",
        "read_agents",
    }
)
ORG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
REPOSITORY = "git@github.com:1-vps/hostpanel.git"
GITHUB_REPOSITORY = "1-vps/hostpanel"
CLUSTER_NAME = "HostPanel"
PIPELINE_NAME = "HostPanel"
PIPELINE_SLUG = "hostpanel"
MAX_CLUSTERS = 100
MAX_PIPELINES = 3000
MAX_QUEUES = 100

QUEUE_DESCRIPTIONS = {
    "hostpanel-upload": "Trusted no-checkout pipeline signer/uploader",
    "hostpanel-ci": "Disposable repository CI workers",
    "hostpanel-qemu": "Disposable KVM post-merge acceptance workers",
}

STATIC_BOOTSTRAP = """steps:
  - label: ":pipeline: Upload reviewed pipeline"
    key: "upload-reviewed-pipeline"
    command: "/usr/local/libexec/hostpanel-upload-pipeline"
    checkout:
      skip: true
    agents:
      queue: "hostpanel-upload"
"""

QUARANTINE_PROVIDER = {
    "filter_enabled": False,
    "build_branches": False,
    "build_pull_requests": False,
    "build_pull_request_forks": False,
    "build_tags": False,
    "ignore_default_branch_pull_requests": False,
    "publish_commit_status": False,
    "publish_commit_status_per_step": False,
    "pull_request_branch_filter_enabled": False,
    "skip_pull_request_builds_for_existing_commits": False,
    "build_pull_request_base_branch_changed": False,
    "build_pull_request_labels_changed": False,
    "build_pull_request_ready_for_review": False,
    "build_pull_request_reopened": False,
    "build_pull_request_edited": False,
    "build_pull_request_converted_to_draft": False,
    "build_pull_request_review_requested": False,
    "build_check_run_completed": False,
    "build_pull_request_review_submitted": False,
    "build_pull_request_review_dismissed": False,
    "build_release_published": False,
    "build_release_created": False,
    "build_release_released": False,
    "build_issue_comment_created": False,
    "build_deployment_status_created": False,
    "build_pull_request_review_comment_created": False,
    "build_pull_request_dequeued": False,
    "build_create_event": False,
    "cancel_deleted_branch_builds": False,
    "build_merge_group_checks_requested": False,
    "cancel_when_merge_group_destroyed": False,
    "use_merge_group_base_commit_for_git_diff_base": False,
    "prefix_pull_request_fork_branch_names": True,
    "publish_blocked_as_pending": True,
    "separate_pull_request_statuses": True,
    "build_pull_request_merge_commits": False,
    "skip_builds_for_existing_commits": False,
    "skip_builds_for_closed_pull_requests": True,
    "use_step_key_as_commit_status": False,
    "trigger_mode": "none",
}

ACTIVE_PROVIDER = {
    **QUARANTINE_PROVIDER,
    "build_branches": True,
    "build_pull_requests": True,
    "publish_commit_status": True,
    "build_pull_request_ready_for_review": True,
    "build_issue_comment_created": True,
    "issue_comment_command_word": "/bk",
    "issue_comment_match_mode": "exact",
    "trigger_mode": "code",
}


class OperatorError(RuntimeError):
    pass


@dataclass
class CreatedResources:
    cluster_uuid: str = ""
    upload_queue_uuid: str = ""
    ci_queue_uuid: str = ""
    qemu_queue_uuid: str = ""
    pipeline_uuid: str = ""
    pipeline_slug: str = ""


@dataclass(frozen=True)
class ActivationIdentity:
    pipeline_uuid: str
    cluster_uuid: str
    upload_queue_uuid: str
    ci_queue_uuid: str
    qemu_queue_uuid: str

    @property
    def queues(self) -> dict[str, str]:
        return {
            "hostpanel-upload": self.upload_queue_uuid,
            "hostpanel-ci": self.ci_queue_uuid,
            "hostpanel-qemu": self.qemu_queue_uuid,
        }


def operator_dir() -> pathlib.Path:
    path = pathlib.Path(__file__).resolve().parent
    verifier = path / "verify-pipeline-state.py"
    try:
        info = os.lstat(verifier)
    except OSError as exc:
        raise OperatorError("pipeline state verifier is unavailable") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise OperatorError("pipeline state verifier must be a regular non-symlink file")
    return path


def load_state_verifier():
    path = operator_dir() / "verify-pipeline-state.py"
    spec = importlib.util.spec_from_file_location("hostpanel_pipeline_state_verifier", path)
    if spec is None or spec.loader is None:
        raise OperatorError("cannot load pipeline state verifier")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def safe_environment() -> dict[str, str]:
    allowed: dict[str, str] = {"PATH": SAFE_PATH}
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
        raise OperatorError("bk CLI is unavailable in the reviewed system PATH")
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
        if len(detail) > 500:
            detail = detail[:500] + "..."
        suffix = f": {detail}" if detail else ""
        raise OperatorError(f"bk command failed with status {result.returncode}{suffix}")
    return result.stdout


def parse_json(raw: str, label: str):
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OperatorError(f"{label} did not return valid JSON") from exc


def canonical_uuid(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise OperatorError(f"{label} is missing")
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise OperatorError(f"{label} is not a UUID") from exc
    canonical = str(parsed)
    if value != canonical:
        raise OperatorError(f"{label} is not in canonical lowercase UUID form")
    return canonical


def top_uuid(raw: str, label: str) -> str:
    payload = parse_json(raw, label)
    if not isinstance(payload, dict):
        raise OperatorError(f"{label} did not return a JSON object")
    return canonical_uuid(payload.get("id") or payload.get("uuid"), f"{label} id")


def preflight(org: str) -> None:
    run_bk(["auth", "switch", org])
    run_bk(["auth", "status", "-o", "json"])
    token = parse_json(run_bk(["api", "/access-token"]), "bk api /access-token")
    if not isinstance(token, dict) or not isinstance(token.get("scopes"), list):
        raise OperatorError("Buildkite access-token response does not contain scopes")
    scopes = {item for item in token["scopes"] if isinstance(item, str)}
    missing = sorted(REQUIRED_SCOPES.difference(scopes))
    if missing:
        raise OperatorError("Buildkite credential is missing required scopes: " + ", ".join(missing))


def extract_list(payload, *, key: str | None, label: str) -> list[dict]:
    if key and isinstance(payload, dict) and isinstance(payload.get(key), list):
        payload = payload[key]
    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
        raise OperatorError(f"{label} returned an unexpected JSON shape")
    return payload


def assert_no_existing_control_plane() -> None:
    clusters = extract_list(
        parse_json(run_bk(["api", f"/clusters?per_page={MAX_CLUSTERS}"]), "cluster inventory"),
        key=None,
        label="cluster inventory",
    )
    if len(clusters) >= MAX_CLUSTERS:
        raise OperatorError("cluster inventory reached the 100-item pagination bound; cannot prove completeness")
    for item in clusters:
        name = item.get("name")
        if isinstance(name, str) and name.casefold() == CLUSTER_NAME.casefold():
            raise OperatorError("an existing HostPanel cluster was found; inspect it instead of creating a duplicate")

    pipelines = extract_list(
        parse_json(
            run_bk(["pipeline", "list", "--limit", str(MAX_PIPELINES), "-o", "json"]),
            "pipeline inventory",
        ),
        key="pipelines",
        label="pipeline inventory",
    )
    if len(pipelines) >= MAX_PIPELINES:
        raise OperatorError("pipeline inventory reached the CLI pagination bound; cannot prove completeness")
    for item in pipelines:
        name = item.get("name")
        if isinstance(name, str) and name.casefold() == PIPELINE_NAME.casefold():
            raise OperatorError("an existing HostPanel pipeline name was found; inspect it instead of creating a duplicate")
        if item.get("slug") == PIPELINE_SLUG:
            raise OperatorError("an existing HostPanel pipeline slug was found; inspect it instead of creating a duplicate")
        if item.get("repository") == REPOSITORY or item.get("repository_url") == REPOSITORY:
            raise OperatorError("an existing pipeline for the HostPanel repository was found; inspect it instead of creating a duplicate")


def parse_queue_inventory(raw: str) -> dict[str, str]:
    queues = extract_list(parse_json(raw, "queue inventory"), key="queues", label="queue inventory")
    if len(queues) >= MAX_QUEUES:
        raise OperatorError("HostPanel queue inventory reached the 100-item bound; cannot prove completeness")
    if len(queues) != len(QUEUE_DESCRIPTIONS):
        raise OperatorError("HostPanel cluster must contain exactly the three reviewed queues")
    mapping: dict[str, str] = {}
    for item in queues:
        key = item.get("key")
        if not isinstance(key, str) or key not in QUEUE_DESCRIPTIONS or key in mapping:
            raise OperatorError("HostPanel queue inventory contains an unreviewed or duplicate key")
        queue_id = canonical_uuid(item.get("id") or item.get("uuid"), f"{key} queue id")
        if item.get("hosted") not in (None, False) or item.get("hosted_agents") is not None:
            raise OperatorError(f"HostPanel queue {key} must be self-hosted")
        if item.get("dispatch_paused") is not False:
            raise OperatorError(f"HostPanel queue {key} must explicitly have dispatch_paused=false")
        mapping[key] = queue_id
    return mapping


def verify_cluster(cluster_id: str) -> dict[str, str]:
    cluster_id = canonical_uuid(cluster_id, "expected cluster id")
    cluster = parse_json(run_bk(["cluster", "view", cluster_id, "-o", "json"]), "cluster view")
    if not isinstance(cluster, dict):
        raise OperatorError("cluster view did not return a JSON object")
    if canonical_uuid(cluster.get("id"), "cluster id") != cluster_id:
        raise OperatorError("HostPanel cluster identity mismatch")
    if cluster.get("name") != CLUSTER_NAME:
        raise OperatorError("HostPanel cluster name mismatch")
    if "default_queue_id" not in cluster or cluster["default_queue_id"] is not None:
        raise OperatorError("HostPanel cluster must explicitly report default_queue_id=null")
    return parse_queue_inventory(
        run_bk(
            [
                "queue",
                "list",
                cluster_id,
                "--limit",
                str(MAX_QUEUES),
                "--per-page",
                str(MAX_QUEUES),
                "-o",
                "json",
            ]
        )
    )


def assert_no_queue_agents(org: str, queues: dict[str, str]) -> None:
    for key in ("hostpanel-upload", "hostpanel-ci", "hostpanel-qemu"):
        queue_id = canonical_uuid(queues[key], f"{key} expected queue id")
        payload = parse_json(
            run_bk(["api", f"/organizations/{org}/agents?cluster_queue_id={queue_id}"]),
            f"agent query for {key}",
        )
        if not isinstance(payload, list):
            raise OperatorError(f"agent query for {key} did not return a JSON list")
        if payload:
            raise OperatorError(f"HostPanel queue {key} already has connected/stopping agents")


def assert_zero_builds(org: str) -> None:
    payload = parse_json(
        run_bk(["build", "list", "--pipeline", f"{org}/{PIPELINE_SLUG}", "--limit", "1", "-o", "json"]),
        "build history",
    )
    builds = extract_list(payload, key="builds", label="build history")
    if builds:
        raise OperatorError("pipeline already has build history; inspect it before activation")


def expected_static_step() -> dict:
    try:
        import yaml
    except ImportError as exc:
        raise OperatorError("python3-yaml is required") from exc
    payload = yaml.safe_load(STATIC_BOOTSTRAP)
    return payload["steps"][0]


def verify_static_bootstrap(pipeline: dict, *, require_signature: bool) -> None:
    try:
        import yaml
    except ImportError as exc:
        raise OperatorError("python3-yaml is required") from exc
    configuration = pipeline.get("configuration")
    if not isinstance(configuration, str):
        raise OperatorError("pipeline configuration is unavailable")
    try:
        actual = yaml.safe_load(configuration)
    except yaml.YAMLError as exc:
        raise OperatorError("pipeline configuration is invalid YAML") from exc
    if not isinstance(actual, dict) or set(actual) != {"steps"}:
        raise OperatorError("pipeline configuration must contain only steps")
    steps = actual.get("steps")
    if not isinstance(steps, list) or len(steps) != 1 or not isinstance(steps[0], dict):
        raise OperatorError("pipeline configuration must contain exactly one bootstrap step")
    step = steps[0]
    allowed = {"label", "key", "command", "checkout", "agents", "signature"}
    if set(step).difference(allowed):
        raise OperatorError("static bootstrap contains unreviewed step fields")
    expected = expected_static_step()
    for key in ("label", "key", "command", "checkout", "agents"):
        if step.get(key) != expected.get(key):
            raise OperatorError(f"static bootstrap {key} mismatch")
    state = load_state_verifier()
    try:
        state.verify_signature_metadata(step, required=require_signature)
    except state.VerificationError as exc:
        raise OperatorError(str(exc)) from exc


def verify_provider_policy(pipeline: dict, expected: dict) -> None:
    provider = pipeline.get("provider")
    if not isinstance(provider, dict) or provider.get("id") != "github":
        raise OperatorError("pipeline is not using the GitHub provider")
    settings = provider.get("settings")
    if not isinstance(settings, dict):
        raise OperatorError("pipeline GitHub provider settings are unavailable")
    for key, value in expected.items():
        if settings.get(key) != value:
            raise OperatorError(f"provider setting mismatch: {key}")
    allowed_true = {key for key, value in expected.items() if value is True}
    for key, value in settings.items():
        if isinstance(value, bool) and value and key not in allowed_true:
            raise OperatorError(f"provider has an unreviewed enabled boolean setting: {key}")


def verify_pipeline(
    raw: str,
    *,
    pipeline_id: str,
    cluster_id: str,
    expected_provider: dict,
    require_signature: bool,
    require_webhook: bool,
) -> dict:
    payload = parse_json(raw, "pipeline view")
    state = load_state_verifier()
    try:
        state.verify_pipeline_state(
            payload,
            expected_pipeline_id=pipeline_id,
            expected_cluster_id=cluster_id,
            require_signature=require_signature,
            require_webhook=require_webhook,
        )
    except state.VerificationError as exc:
        raise OperatorError(str(exc)) from exc
    verify_static_bootstrap(payload, require_signature=require_signature)
    verify_provider_policy(payload, expected_provider)
    return payload


def pipeline_payload(cluster_id: str) -> str:
    cluster_id = canonical_uuid(cluster_id, "pipeline cluster id")
    body = {
        "name": PIPELINE_NAME,
        "slug": PIPELINE_SLUG,
        "cluster_id": cluster_id,
        "repository": REPOSITORY,
        "branch_configuration": "main",
        "default_branch": "main",
        "visibility": "private",
        "env": {},
        "allow_rebuilds": False,
        "pipeline_template_uuid": None,
        "skip_queued_branch_builds": False,
        "skip_queued_branch_builds_filter": None,
        "cancel_running_branch_builds": False,
        "cancel_running_branch_builds_filter": None,
        "configuration": STATIC_BOOTSTRAP,
        "provider_settings": QUARANTINE_PROVIDER,
    }
    return json.dumps(body, separators=(",", ":"))


def print_create_plan(org: str) -> None:
    print("HostPanel Buildkite control-plane creation plan")
    print(f"organization: {org}")
    print(f"repository:   {REPOSITORY}")
    print("No agents are started by this tool.")
    print("Preflight scopes, refuse duplicates, create one cluster and exactly three self-hosted queues.")
    print("Create pipeline hostpanel with trigger_mode=none, no template, no rebuild/skip/cancel behavior.")
    print("Reverify exact provider repository, private pipeline state, zero builds and zero queue agents.")
    print("Record every created UUID for the separate activation command.")
    print("MANDATORY STOP: sign Pipeline Settings and install only the public read-only checkout deploy key.")


def print_activation_plan(org: str, identity: ActivationIdentity) -> None:
    print("HostPanel Buildkite activation plan")
    print(f"organization: {org}")
    print(f"pipeline:     {PIPELINE_SLUG}")
    print(f"pipeline_uuid={identity.pipeline_uuid}")
    print(f"cluster_uuid={identity.cluster_uuid}")
    for key, value in identity.queues.items():
        print(f"{key}_uuid={value}")
    print("No agents are started by this tool.")
    print("Reverify every resource UUID, signed static bootstrap metadata and provider lifecycle while quarantined.")
    print("Reuse or create one valid Buildkite webhook, then PATCH provider_settings to trigger_mode=code.")
    print("Reverify the same resource UUIDs, active policy, strict webhook URL, zero builds and zero queue agents.")
    print("Cryptographic signature validity is enforced by Buildkite agents using verification JWKS with block behavior.")


def create_control_plane(org: str) -> CreatedResources:
    resources = CreatedResources()
    try:
        preflight(org)
        assert_no_existing_control_plane()
        resources.cluster_uuid = top_uuid(
            run_bk(
                [
                    "cluster",
                    "create",
                    "--name",
                    CLUSTER_NAME,
                    "--description",
                    "Disposable hardened HostPanel CI",
                    "-o",
                    "json",
                ]
            ),
            "bk cluster create",
        )

        queue_ids: dict[str, str] = {}
        for key, description in QUEUE_DESCRIPTIONS.items():
            queue_ids[key] = top_uuid(
                run_bk(
                    [
                        "queue",
                        "create",
                        resources.cluster_uuid,
                        "--key",
                        key,
                        "--description",
                        description,
                        "-o",
                        "json",
                    ]
                ),
                f"bk queue create {key}",
            )
        resources.upload_queue_uuid = queue_ids["hostpanel-upload"]
        resources.ci_queue_uuid = queue_ids["hostpanel-ci"]
        resources.qemu_queue_uuid = queue_ids["hostpanel-qemu"]

        verified_queues = verify_cluster(resources.cluster_uuid)
        if verified_queues != queue_ids:
            raise OperatorError("re-fetched HostPanel queue UUIDs do not match created queue UUIDs")
        assert_no_queue_agents(org, verified_queues)

        created = parse_json(
            run_bk(["api", "--method", "POST", "/pipelines", "--data", pipeline_payload(resources.cluster_uuid)]),
            "pipeline creation",
        )
        if not isinstance(created, dict):
            raise OperatorError("pipeline creation did not return a JSON object")
        resources.pipeline_uuid = canonical_uuid(created.get("id"), "created pipeline id")
        if created.get("slug") != PIPELINE_SLUG:
            raise OperatorError("pipeline creation did not return the reviewed hostpanel slug")
        resources.pipeline_slug = PIPELINE_SLUG

        verify_pipeline(
            run_bk(["pipeline", "view", f"{org}/{PIPELINE_SLUG}", "-o", "json"]),
            pipeline_id=resources.pipeline_uuid,
            cluster_id=resources.cluster_uuid,
            expected_provider=QUARANTINE_PROVIDER,
            require_signature=False,
            require_webhook=False,
        )
        assert_zero_builds(org)
        assert_no_queue_agents(org, verified_queues)
        return resources
    except Exception:
        print("Control-plane creation stopped. Created non-secret resource identifiers, if any:", file=sys.stderr)
        for name, value in resources.__dict__.items():
            print(f"  {name}={value}", file=sys.stderr)
        print("No automatic cleanup was attempted; inspect Buildkite before changing partial resources.", file=sys.stderr)
        raise


def activate_control_plane(org: str, identity: ActivationIdentity) -> None:
    preflight(org)
    raw = run_bk(["pipeline", "view", f"{org}/{PIPELINE_SLUG}", "-o", "json"])
    first = parse_json(raw, "pipeline view")
    if not isinstance(first, dict):
        raise OperatorError("pipeline view did not return a JSON object")
    if canonical_uuid(first.get("id"), "pipeline id") != identity.pipeline_uuid:
        raise OperatorError("pipeline UUID changed between creation and activation")
    if canonical_uuid(first.get("cluster_id"), "pipeline cluster id") != identity.cluster_uuid:
        raise OperatorError("pipeline cluster UUID changed between creation and activation")
    queues = verify_cluster(identity.cluster_uuid)
    if queues != identity.queues:
        raise OperatorError("HostPanel queue UUIDs changed between creation and activation")
    assert_no_queue_agents(org, queues)
    assert_zero_builds(org)
    pipeline = verify_pipeline(
        raw,
        pipeline_id=identity.pipeline_uuid,
        cluster_id=identity.cluster_uuid,
        expected_provider=QUARANTINE_PROVIDER,
        require_signature=True,
        require_webhook=False,
    )

    if pipeline["provider"]["webhook_url"] in (None, ""):
        run_bk(["api", "--method", "POST", f"/pipelines/{PIPELINE_SLUG}/webhook"])

    verify_pipeline(
        run_bk(["pipeline", "view", f"{org}/{PIPELINE_SLUG}", "-o", "json"]),
        pipeline_id=identity.pipeline_uuid,
        cluster_id=identity.cluster_uuid,
        expected_provider=QUARANTINE_PROVIDER,
        require_signature=True,
        require_webhook=True,
    )
    if verify_cluster(identity.cluster_uuid) != identity.queues:
        raise OperatorError("HostPanel queue UUIDs changed before provider activation")
    assert_no_queue_agents(org, identity.queues)
    assert_zero_builds(org)

    patch = json.dumps({"provider_settings": ACTIVE_PROVIDER}, separators=(",", ":"))
    run_bk(["api", "--method", "PATCH", f"/pipelines/{PIPELINE_SLUG}", "--data", patch])

    verify_pipeline(
        run_bk(["pipeline", "view", f"{org}/{PIPELINE_SLUG}", "-o", "json"]),
        pipeline_id=identity.pipeline_uuid,
        cluster_id=identity.cluster_uuid,
        expected_provider=ACTIVE_PROVIDER,
        require_signature=True,
        require_webhook=True,
    )
    if verify_cluster(identity.cluster_uuid) != identity.queues:
        raise OperatorError("HostPanel queue UUIDs changed after provider activation")
    assert_no_queue_agents(org, identity.queues)
    assert_zero_builds(org)


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(description="Fail-closed HostPanel Buildkite control-plane operator")
    top.add_argument("--org", required=True)
    top.add_argument("--apply", action="store_true")
    top.add_argument("--confirm-create", action="store_true")
    top.add_argument("--enable-webhook")
    top.add_argument("--confirm-static-bootstrap-signed", action="store_true")
    top.add_argument("--confirm-public-deploy-key-added", action="store_true")
    top.add_argument("--pipeline-id")
    top.add_argument("--cluster-id")
    top.add_argument("--upload-queue-id")
    top.add_argument("--ci-queue-id")
    top.add_argument("--qemu-queue-id")
    return top


def activation_identity(args: argparse.Namespace) -> ActivationIdentity:
    fields = {
        "pipeline_uuid": args.pipeline_id,
        "cluster_uuid": args.cluster_id,
        "upload_queue_uuid": args.upload_queue_id,
        "ci_queue_uuid": args.ci_queue_id,
        "qemu_queue_uuid": args.qemu_queue_id,
    }
    missing = [name for name, value in fields.items() if value is None]
    if missing:
        raise OperatorError("activation requires exact created resource UUIDs: " + ", ".join(missing))
    return ActivationIdentity(
        pipeline_uuid=canonical_uuid(fields["pipeline_uuid"], "--pipeline-id"),
        cluster_uuid=canonical_uuid(fields["cluster_uuid"], "--cluster-id"),
        upload_queue_uuid=canonical_uuid(fields["upload_queue_uuid"], "--upload-queue-id"),
        ci_queue_uuid=canonical_uuid(fields["ci_queue_uuid"], "--ci-queue-id"),
        qemu_queue_uuid=canonical_uuid(fields["qemu_queue_uuid"], "--qemu-queue-id"),
    )


def validate_args(args: argparse.Namespace) -> ActivationIdentity | None:
    if ORG_RE.fullmatch(args.org) is None:
        raise OperatorError("invalid --org")
    identity_values = (
        args.pipeline_id,
        args.cluster_id,
        args.upload_queue_id,
        args.ci_queue_id,
        args.qemu_queue_id,
    )
    if args.enable_webhook is not None:
        if args.enable_webhook != PIPELINE_SLUG:
            raise OperatorError("--enable-webhook must target the reviewed hostpanel pipeline slug")
        if args.confirm_create:
            raise OperatorError("--confirm-create is not valid with --enable-webhook")
        return activation_identity(args)
    if any(value is not None for value in identity_values):
        raise OperatorError("resource UUID arguments are only valid with --enable-webhook")
    if args.confirm_static_bootstrap_signed:
        raise OperatorError("--confirm-static-bootstrap-signed requires --enable-webhook")
    if args.confirm_public_deploy_key_added:
        raise OperatorError("--confirm-public-deploy-key-added requires --enable-webhook")
    return None


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        identity = validate_args(args)
        if not args.apply:
            if identity is not None:
                print_activation_plan(args.org, identity)
            else:
                print_create_plan(args.org)
            return 0

        if identity is None:
            if not args.confirm_create:
                raise OperatorError("--apply requires --confirm-create")
            resources = create_control_plane(args.org)
            print("HostPanel Buildkite control plane created in GitHub-trigger quarantine.")
            for name, value in resources.__dict__.items():
                print(f"{name}={value}")
            print("provider_trigger_mode=none")
            print("MANDATORY STOP: record these UUIDs; do not connect an agent or activate GitHub triggers yet.")
            return 0

        if not args.confirm_static_bootstrap_signed:
            raise OperatorError("--enable-webhook with --apply requires --confirm-static-bootstrap-signed")
        if not args.confirm_public_deploy_key_added:
            raise OperatorError("--enable-webhook with --apply requires --confirm-public-deploy-key-added")
        activate_control_plane(args.org, identity)
        print("HostPanel Buildkite pipeline activation verified.")
        print(f"organization={args.org}")
        print(f"pipeline_slug={PIPELINE_SLUG}")
        print(f"pipeline_uuid={identity.pipeline_uuid}")
        print(f"cluster_uuid={identity.cluster_uuid}")
        print(f"repository={REPOSITORY}")
        print("provider_trigger_mode=code")
        print("No agents were started.")
        return 0
    except OperatorError as exc:
        print(f"HostPanel Buildkite control-plane operator failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
