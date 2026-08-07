#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import uuid
from urllib.parse import urlsplit

EXPECTED_NAME = "HostPanel"
EXPECTED_SLUG = "hostpanel"
EXPECTED_REPOSITORY = "git@github.com:1-vps/hostpanel.git"
EXPECTED_PROVIDER_REPOSITORY = "1-vps/hostpanel"
EXPECTED_SIGNING_KEY_ID = "hostpanel-2026-08"
EXPECTED_SIGNED_FIELDS = frozenset(
    {"command", "env", "matrix", "plugins", "repository_url"}
)
WEBHOOK_PATH_RE = re.compile(r"^/deliver/[A-Za-z0-9._~-]+$")
BASE64URL_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_-]+$")
REQUIRED_PIPELINE_STATE_FIELDS = frozenset(
    {
        "id",
        "name",
        "slug",
        "repository",
        "cluster_id",
        "visibility",
        "env",
        "branch_configuration",
        "default_branch",
        "allow_rebuilds",
        "archived_at",
        "pipeline_template_uuid",
        "skip_queued_branch_builds",
        "skip_queued_branch_builds_filter",
        "cancel_running_branch_builds",
        "cancel_running_branch_builds_filter",
        "provider",
        "configuration",
    }
)


class VerificationError(RuntimeError):
    pass


def canonical_uuid(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise VerificationError(f"{label} is missing")
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise VerificationError(f"{label} is not a UUID") from exc
    canonical = str(parsed)
    if value != canonical:
        raise VerificationError(f"{label} is not in canonical lowercase UUID form")
    return canonical


def verify_webhook_url(value: object) -> None:
    if not isinstance(value, str) or not value:
        raise VerificationError("Buildkite webhook URL is missing")
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as exc:
        raise VerificationError("Buildkite webhook URL contains an invalid port") from exc
    if parsed.scheme != "https":
        raise VerificationError("Buildkite webhook URL must use https")
    if parsed.hostname != "webhook.buildkite.com":
        raise VerificationError("Buildkite webhook URL host is unexpected")
    if parsed.username is not None or parsed.password is not None or port is not None:
        raise VerificationError("Buildkite webhook URL must not contain userinfo or an explicit port")
    if parsed.query or parsed.fragment:
        raise VerificationError("Buildkite webhook URL must not contain a query or fragment")
    if WEBHOOK_PATH_RE.fullmatch(parsed.path) is None:
        raise VerificationError("Buildkite webhook URL path is unexpected")


def decode_base64url_segment(value: str, label: str) -> bytes:
    if BASE64URL_SEGMENT_RE.fullmatch(value) is None:
        raise VerificationError(f"{label} contains invalid base64url characters")
    padded = value + "=" * (-len(value) % 4)
    try:
        return base64.b64decode(padded, altchars=b"-_", validate=True)
    except (ValueError, TypeError, base64.binascii.Error) as exc:
        raise VerificationError(f"{label} is invalid base64url") from exc


def decode_jws_header(value: str) -> dict:
    parts = value.split(".")
    if len(parts) != 3 or parts[1] != "" or not parts[0] or not parts[2]:
        raise VerificationError("static bootstrap signature is not detached compact JWS")
    header_bytes = decode_base64url_segment(parts[0], "static bootstrap JWS header")
    decode_base64url_segment(parts[2], "static bootstrap JWS signature")
    try:
        header = json.loads(header_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerificationError("static bootstrap JWS header is invalid JSON") from exc
    if not isinstance(header, dict):
        raise VerificationError("static bootstrap JWS header is not an object")
    if set(header) != {"alg", "kid"}:
        raise VerificationError("static bootstrap JWS header contains unreviewed parameters")
    return header


def verify_signature_metadata(step: dict, *, required: bool) -> None:
    signature = step.get("signature")
    if not required:
        if signature is not None:
            raise VerificationError("unsigned quarantine bootstrap unexpectedly contains a signature")
        return
    if not isinstance(signature, dict):
        raise VerificationError("static bootstrap signature is missing")
    if set(signature) != {"algorithm", "signed_fields", "value"}:
        raise VerificationError("static bootstrap signature shape is unexpected")
    if signature.get("algorithm") != "EdDSA":
        raise VerificationError("static bootstrap signature algorithm is not EdDSA")
    signed_fields = signature.get("signed_fields")
    if not isinstance(signed_fields, list) or not all(
        isinstance(item, str) for item in signed_fields
    ):
        raise VerificationError("static bootstrap signed_fields is malformed")
    if len(signed_fields) != len(set(signed_fields)):
        raise VerificationError("static bootstrap signed_fields contains duplicates")
    if frozenset(signed_fields) != EXPECTED_SIGNED_FIELDS:
        raise VerificationError("static bootstrap signed_fields does not match Buildkite's reviewed signed surface")
    value = signature.get("value")
    if not isinstance(value, str):
        raise VerificationError("static bootstrap signature value is missing")
    header = decode_jws_header(value)
    if header.get("alg") != "EdDSA":
        raise VerificationError("static bootstrap JWS algorithm mismatch")
    if header.get("kid") != EXPECTED_SIGNING_KEY_ID:
        raise VerificationError("static bootstrap JWS key ID mismatch")


def verify_pipeline_state(
    pipeline: object,
    *,
    expected_pipeline_id: str,
    expected_cluster_id: str,
    require_signature: bool,
    require_webhook: bool,
) -> None:
    if not isinstance(pipeline, dict):
        raise VerificationError("pipeline response is not a JSON object")
    missing = sorted(REQUIRED_PIPELINE_STATE_FIELDS.difference(pipeline))
    if missing:
        raise VerificationError("pipeline response is missing required state fields: " + ", ".join(missing))
    expected_pipeline_id = canonical_uuid(expected_pipeline_id, "expected pipeline id")
    expected_cluster_id = canonical_uuid(expected_cluster_id, "expected cluster id")
    if canonical_uuid(pipeline["id"], "pipeline id") != expected_pipeline_id:
        raise VerificationError("pipeline id mismatch")
    if pipeline["name"] != EXPECTED_NAME or pipeline["slug"] != EXPECTED_SLUG:
        raise VerificationError("pipeline name or slug mismatch")
    if pipeline["repository"] != EXPECTED_REPOSITORY:
        raise VerificationError("pipeline repository mismatch")
    actual_cluster_id = canonical_uuid(pipeline["cluster_id"], "pipeline cluster id")
    if actual_cluster_id != expected_cluster_id:
        raise VerificationError("pipeline cluster id mismatch")
    if pipeline["visibility"] != "private":
        raise VerificationError("pipeline visibility must remain private")
    if pipeline["env"] not in ({}, None):
        raise VerificationError("pipeline-level environment must be empty")
    if pipeline["branch_configuration"] != "main":
        raise VerificationError("pipeline branch configuration must be main")
    if pipeline["default_branch"] != "main":
        raise VerificationError("pipeline default branch must be main")
    if pipeline["allow_rebuilds"] is not False:
        raise VerificationError("pipeline rebuilds must be disabled")
    if pipeline["archived_at"] is not None:
        raise VerificationError("pipeline must not be archived")
    if pipeline["pipeline_template_uuid"] is not None:
        raise VerificationError("signed HostPanel pipeline must not use a pipeline template")
    if pipeline["skip_queued_branch_builds"] is not False:
        raise VerificationError("skip_queued_branch_builds must be false")
    if pipeline["skip_queued_branch_builds_filter"] is not None:
        raise VerificationError("skip_queued_branch_builds_filter must be null")
    if pipeline["cancel_running_branch_builds"] is not False:
        raise VerificationError("cancel_running_branch_builds must be false")
    if pipeline["cancel_running_branch_builds_filter"] is not None:
        raise VerificationError("cancel_running_branch_builds_filter must be null")

    provider = pipeline["provider"]
    if not isinstance(provider, dict) or provider.get("id") != "github":
        raise VerificationError("pipeline is not using the GitHub provider")
    if "settings" not in provider or "webhook_url" not in provider:
        raise VerificationError("pipeline GitHub provider response is incomplete")
    settings = provider["settings"]
    if not isinstance(settings, dict):
        raise VerificationError("pipeline GitHub provider settings are unavailable")
    if settings.get("repository") != EXPECTED_PROVIDER_REPOSITORY:
        raise VerificationError("GitHub provider repository is not 1-vps/hostpanel")

    webhook = provider["webhook_url"]
    if require_webhook:
        verify_webhook_url(webhook)
    elif webhook not in (None, ""):
        verify_webhook_url(webhook)

    configuration = pipeline["configuration"]
    if not isinstance(configuration, str):
        raise VerificationError("pipeline YAML configuration is unavailable")
    try:
        import yaml
    except ImportError as exc:
        raise VerificationError("python3-yaml is required") from exc
    try:
        document = yaml.safe_load(configuration)
    except yaml.YAMLError as exc:
        raise VerificationError("pipeline configuration is invalid YAML") from exc
    if not isinstance(document, dict) or set(document) != {"steps"}:
        raise VerificationError("pipeline configuration must contain only steps")
    steps = document.get("steps")
    if not isinstance(steps, list) or len(steps) != 1 or not isinstance(steps[0], dict):
        raise VerificationError("pipeline configuration must contain exactly one bootstrap step")
    verify_signature_metadata(steps[0], required=require_signature)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fail-closed HostPanel Buildkite pipeline state verifier")
    parser.add_argument("--pipeline-id", required=True)
    parser.add_argument("--cluster-id", required=True)
    parser.add_argument("--require-signature", action="store_true")
    parser.add_argument("--require-webhook", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        pipeline_id = canonical_uuid(args.pipeline_id, "--pipeline-id")
        cluster_id = canonical_uuid(args.cluster_id, "--cluster-id")
        raw = sys.stdin.read(4 * 1024 * 1024 + 1)
        if not raw or len(raw) > 4 * 1024 * 1024:
            raise VerificationError("pipeline JSON input is empty or too large")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise VerificationError("pipeline JSON input is invalid") from exc
        verify_pipeline_state(
            payload,
            expected_pipeline_id=pipeline_id,
            expected_cluster_id=cluster_id,
            require_signature=args.require_signature,
            require_webhook=args.require_webhook,
        )
    except VerificationError as exc:
        print(f"HostPanel Buildkite pipeline state rejected: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
