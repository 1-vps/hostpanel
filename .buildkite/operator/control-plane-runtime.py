#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import stat
import sys


class RuntimeErrorSafe(RuntimeError):
    pass


def load_base():
    here = pathlib.Path(__file__).resolve().parent
    path = here / "bootstrap-control-plane.py"
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise RuntimeErrorSafe("control-plane base operator is unavailable") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise RuntimeErrorSafe("control-plane base operator must be a regular non-symlink file")
    spec = importlib.util.spec_from_file_location("hostpanel_control_plane_base", path)
    if spec is None or spec.loader is None:
        raise RuntimeErrorSafe("cannot load control-plane base operator")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


base = load_base()


def fetch_pipeline_raw() -> str:
    # Use the documented REST resource directly. `bk pipeline view -o json`
    # documents JSON output but not that its projection is a stable full REST
    # pipeline shape.
    return base.run_bk(["api", f"/pipelines/{base.PIPELINE_SLUG}"])


def pipeline_state_patch(provider_settings: dict) -> str:
    return json.dumps(
        {
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
            "provider_settings": provider_settings,
        },
        separators=(",", ":"),
    )


def patch_pipeline_state(provider_settings: dict) -> str:
    return base.run_bk(
        [
            "api",
            "--method",
            "PATCH",
            f"/pipelines/{base.PIPELINE_SLUG}",
            "--data",
            pipeline_state_patch(provider_settings),
        ]
    )


def verify_persisted_core(
    raw: str,
    *,
    identity,
    expected_provider: dict,
    require_signature: bool,
    require_webhook: bool,
) -> dict:
    payload = base.parse_json(raw, "pipeline GET")
    if not isinstance(payload, dict):
        raise base.OperatorError("pipeline GET did not return a JSON object")
    if base.canonical_uuid(payload.get("id"), "pipeline id") != identity.pipeline_uuid:
        raise base.OperatorError("pipeline UUID changed")
    if base.canonical_uuid(payload.get("cluster_id"), "pipeline cluster id") != identity.cluster_uuid:
        raise base.OperatorError("pipeline cluster UUID changed")
    if payload.get("name") != base.PIPELINE_NAME or payload.get("slug") != base.PIPELINE_SLUG:
        raise base.OperatorError("pipeline name or slug mismatch")
    if payload.get("repository") != base.REPOSITORY:
        raise base.OperatorError("pipeline repository mismatch")
    if payload.get("visibility") != "private":
        raise base.OperatorError("pipeline visibility must remain private")
    if payload.get("env") not in ({}, None):
        raise base.OperatorError("pipeline-level environment must be empty")
    if payload.get("branch_configuration") != "main" or payload.get("default_branch") != "main":
        raise base.OperatorError("pipeline branch/default branch state mismatch")
    if payload.get("archived_at") is not None:
        raise base.OperatorError("pipeline must not be archived")

    base.verify_static_bootstrap(payload, require_signature=require_signature)
    base.verify_provider_policy(payload, expected_provider)
    provider = payload.get("provider")
    if not isinstance(provider, dict):
        raise base.OperatorError("pipeline provider is unavailable")
    state = base.load_state_verifier()
    webhook = provider.get("webhook_url")
    if require_webhook or webhook not in (None, ""):
        try:
            state.verify_webhook_url(webhook)
        except state.VerificationError as exc:
            raise base.OperatorError(str(exc)) from exc
    return payload


def verify_full_patch_response(
    raw: str,
    *,
    identity,
    expected_provider: dict,
    require_signature: bool,
    require_webhook: bool,
) -> dict:
    return base.verify_pipeline(
        raw,
        pipeline_id=identity.pipeline_uuid,
        cluster_id=identity.cluster_uuid,
        expected_provider=expected_provider,
        require_signature=require_signature,
        require_webhook=require_webhook,
    )


def create_control_plane(org: str):
    resources = base.CreatedResources()
    try:
        base.preflight(org)
        base.assert_no_existing_control_plane()
        resources.cluster_uuid = base.top_uuid(
            base.run_bk(
                [
                    "cluster",
                    "create",
                    "--name",
                    base.CLUSTER_NAME,
                    "--description",
                    "Disposable hardened HostPanel CI",
                    "-o",
                    "json",
                ]
            ),
            "bk cluster create",
        )

        queue_ids: dict[str, str] = {}
        for key, description in base.QUEUE_DESCRIPTIONS.items():
            queue_ids[key] = base.top_uuid(
                base.run_bk(
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

        verified_queues = base.verify_cluster(resources.cluster_uuid)
        if verified_queues != queue_ids:
            raise base.OperatorError("re-fetched HostPanel queue UUIDs do not match created queue UUIDs")
        base.assert_no_queue_agents(org, verified_queues)

        created = base.parse_json(
            base.run_bk(
                [
                    "api",
                    "--method",
                    "POST",
                    "/pipelines",
                    "--data",
                    base.pipeline_payload(resources.cluster_uuid),
                ]
            ),
            "pipeline creation",
        )
        if not isinstance(created, dict):
            raise base.OperatorError("pipeline creation did not return a JSON object")
        resources.pipeline_uuid = base.canonical_uuid(created.get("id"), "created pipeline id")
        if created.get("slug") != base.PIPELINE_SLUG:
            raise base.OperatorError("pipeline creation did not return the reviewed hostpanel slug")
        resources.pipeline_slug = base.PIPELINE_SLUG
        identity = base.ActivationIdentity(
            resources.pipeline_uuid,
            resources.cluster_uuid,
            resources.upload_queue_uuid,
            resources.ci_queue_uuid,
            resources.qemu_queue_uuid,
        )

        verify_persisted_core(
            fetch_pipeline_raw(),
            identity=identity,
            expected_provider=base.QUARANTINE_PROVIDER,
            require_signature=False,
            require_webhook=False,
        )
        # Explicitly normalize every mutable lifecycle control while triggers
        # remain quarantined. In particular, this writes template UUID null and
        # disables rebuild/skip/cancel behavior even if a GET representation
        # omits one of those fields.
        verify_full_patch_response(
            patch_pipeline_state(base.QUARANTINE_PROVIDER),
            identity=identity,
            expected_provider=base.QUARANTINE_PROVIDER,
            require_signature=False,
            require_webhook=False,
        )
        base.assert_zero_builds(org)
        base.assert_no_queue_agents(org, verified_queues)
        return resources
    except Exception:
        print("Control-plane creation stopped. Created non-secret resource identifiers, if any:", file=sys.stderr)
        for name, value in resources.__dict__.items():
            print(f"  {name}={value}", file=sys.stderr)
        print("No automatic resource deletion was attempted; inspect Buildkite before changing partial resources.", file=sys.stderr)
        raise


def activate_control_plane(org: str, identity) -> None:
    base.preflight(org)
    queues = base.verify_cluster(identity.cluster_uuid)
    if queues != identity.queues:
        raise base.OperatorError("HostPanel queue UUIDs changed between creation and activation")
    base.assert_no_queue_agents(org, queues)
    base.assert_zero_builds(org)

    verify_persisted_core(
        fetch_pipeline_raw(),
        identity=identity,
        expected_provider=base.QUARANTINE_PROVIDER,
        require_signature=True,
        require_webhook=False,
    )

    normalized = patch_pipeline_state(base.QUARANTINE_PROVIDER)
    verify_full_patch_response(
        normalized,
        identity=identity,
        expected_provider=base.QUARANTINE_PROVIDER,
        require_signature=True,
        require_webhook=False,
    )
    if base.verify_cluster(identity.cluster_uuid) != identity.queues:
        raise base.OperatorError("HostPanel queue UUIDs changed during quarantine normalization")
    base.assert_no_queue_agents(org, identity.queues)
    base.assert_zero_builds(org)

    provider = base.parse_json(normalized, "quarantine PATCH").get("provider")
    webhook = provider.get("webhook_url") if isinstance(provider, dict) else None
    if webhook in (None, ""):
        base.run_bk(["api", "--method", "POST", f"/pipelines/{base.PIPELINE_SLUG}/webhook"])

    # Re-normalize after webhook setup so webhook creation cannot become a
    # lifecycle-drift window before GitHub triggers are enabled.
    normalized = patch_pipeline_state(base.QUARANTINE_PROVIDER)
    verify_full_patch_response(
        normalized,
        identity=identity,
        expected_provider=base.QUARANTINE_PROVIDER,
        require_signature=True,
        require_webhook=True,
    )
    verify_persisted_core(
        fetch_pipeline_raw(),
        identity=identity,
        expected_provider=base.QUARANTINE_PROVIDER,
        require_signature=True,
        require_webhook=True,
    )
    if base.verify_cluster(identity.cluster_uuid) != identity.queues:
        raise base.OperatorError("HostPanel queue UUIDs changed before provider activation")
    base.assert_no_queue_agents(org, identity.queues)
    base.assert_zero_builds(org)

    active_patch_sent = False
    try:
        active_patch_sent = True
        verify_full_patch_response(
            patch_pipeline_state(base.ACTIVE_PROVIDER),
            identity=identity,
            expected_provider=base.ACTIVE_PROVIDER,
            require_signature=True,
            require_webhook=True,
        )
        verify_persisted_core(
            fetch_pipeline_raw(),
            identity=identity,
            expected_provider=base.ACTIVE_PROVIDER,
            require_signature=True,
            require_webhook=True,
        )
        if base.verify_cluster(identity.cluster_uuid) != identity.queues:
            raise base.OperatorError("HostPanel queue UUIDs changed after provider activation")
        base.assert_no_queue_agents(org, identity.queues)
        base.assert_zero_builds(org)
    except Exception as exc:
        if active_patch_sent:
            try:
                verify_full_patch_response(
                    patch_pipeline_state(base.QUARANTINE_PROVIDER),
                    identity=identity,
                    expected_provider=base.QUARANTINE_PROVIDER,
                    require_signature=True,
                    require_webhook=True,
                )
                verify_persisted_core(
                    fetch_pipeline_raw(),
                    identity=identity,
                    expected_provider=base.QUARANTINE_PROVIDER,
                    require_signature=True,
                    require_webhook=True,
                )
            except Exception as rollback_exc:
                raise base.OperatorError(
                    "provider activation verification failed and automatic quarantine rollback "
                    "could not be proven; immediately set trigger_mode=none manually"
                ) from rollback_exc
        raise exc


def main(argv: list[str] | None = None) -> int:
    args = base.parser().parse_args(argv)
    try:
        identity = base.validate_args(args)
        if not args.apply:
            if identity is None:
                base.print_create_plan(args.org)
            else:
                base.print_activation_plan(args.org, identity)
            return 0

        if identity is None:
            if not args.confirm_create:
                raise base.OperatorError("--apply requires --confirm-create")
            resources = create_control_plane(args.org)
            print("HostPanel Buildkite control plane created in GitHub-trigger quarantine.")
            for name, value in resources.__dict__.items():
                print(f"{name}={value}")
            print("provider_trigger_mode=none")
            print("MANDATORY STOP: record these UUIDs; do not connect an agent or activate GitHub triggers yet.")
            return 0

        if not args.confirm_static_bootstrap_signed:
            raise base.OperatorError("--enable-webhook with --apply requires --confirm-static-bootstrap-signed")
        if not args.confirm_public_deploy_key_added:
            raise base.OperatorError("--enable-webhook with --apply requires --confirm-public-deploy-key-added")
        activate_control_plane(args.org, identity)
        print("HostPanel Buildkite pipeline activation verified.")
        print(f"organization={args.org}")
        print(f"pipeline_slug={base.PIPELINE_SLUG}")
        print(f"pipeline_uuid={identity.pipeline_uuid}")
        print(f"cluster_uuid={identity.cluster_uuid}")
        print(f"repository={base.REPOSITORY}")
        print("provider_trigger_mode=code")
        print("No agents were started.")
        return 0
    except (base.OperatorError, RuntimeErrorSafe) as exc:
        print(f"HostPanel Buildkite control-plane runtime failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
