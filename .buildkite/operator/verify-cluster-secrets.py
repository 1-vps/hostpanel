#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
import uuid

REQUIRED_SCOPE = "read_secret_details"
MAX_SECRETS = 100
ORG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class SecretVerificationError(RuntimeError):
    pass


def canonical_uuid(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise SecretVerificationError(f"{label} is missing")
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise SecretVerificationError(f"{label} is not a UUID") from exc
    canonical = str(parsed)
    if value != canonical:
        raise SecretVerificationError(f"{label} is not canonical lowercase UUID form")
    return canonical


def endpoint(org: str, cluster_id: str) -> str:
    if ORG_RE.fullmatch(org) is None:
        raise SecretVerificationError("organization slug is malformed")
    cluster_id = canonical_uuid(cluster_id, "cluster id")
    return f"/organizations/{org}/clusters/{cluster_id}/secrets?per_page={MAX_SECRETS}"


def verify_scope_payload(raw: str) -> None:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SecretVerificationError("access-token scope response is invalid JSON") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("scopes"), list):
        raise SecretVerificationError("access-token scope response does not contain scopes")
    scopes = payload["scopes"]
    if not all(isinstance(item, str) for item in scopes):
        raise SecretVerificationError("access-token scopes contain a non-string value")
    if REQUIRED_SCOPE not in scopes:
        raise SecretVerificationError(
            f"Buildkite credential is missing required scope: {REQUIRED_SCOPE}"
        )


def verify_empty_inventory(raw: str) -> None:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SecretVerificationError("cluster secrets inventory is invalid JSON") from exc
    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
        raise SecretVerificationError("cluster secrets inventory has an unexpected JSON shape")
    if len(payload) >= MAX_SECRETS:
        raise SecretVerificationError(
            "cluster secrets inventory reached the 100-item pagination bound; "
            "cannot prove inventory completeness"
        )
    if payload:
        keys = []
        for item in payload[:10]:
            key = item.get("key")
            keys.append(key if isinstance(key, str) and key else "<unknown>")
        suffix = ", ".join(keys)
        raise SecretVerificationError(
            f"HostPanel cluster contains Buildkite Secrets ({suffix}); remove/review all secrets "
            "before activation or worker connection"
        )


def main() -> int:
    raw = sys.stdin.read(1024 * 1024 + 1)
    if not raw or len(raw) > 1024 * 1024:
        print("HostPanel Buildkite secret verifier rejected empty/oversized input", file=sys.stderr)
        return 1
    try:
        verify_empty_inventory(raw)
    except SecretVerificationError as exc:
        print(f"HostPanel Buildkite secret verifier rejected cluster: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
