#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import ipaddress
import json
import os
import pathlib
import re
import shutil
import stat
import subprocess
import sys
import uuid

SAFE_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
REQUIRED_SCOPES = frozenset({"read_clusters", "write_clusters"})
WORKER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
ORG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
DESCRIPTION_PREFIX = "hostpanel-worker:"
MIN_TTL_MINUTES = 5
MAX_TTL_MINUTES = 60


class OperatorError(RuntimeError):
    pass


def canonical_uuid(value: str, label: str) -> str:
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise OperatorError(f"{label} must be a canonical UUID") from exc
    canonical = str(parsed)
    if value.lower() != canonical:
        raise OperatorError(f"{label} must use canonical UUID form")
    return canonical


def exact_ipv4_cidr(value: str) -> str:
    if "/" in value:
        raise OperatorError("--allowed-ip must be one bare IPv4 address, not a CIDR range")
    try:
        address = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError as exc:
        raise OperatorError("--allowed-ip must be a valid IPv4 address") from exc
    return f"{address}/32"


def isoformat_z(value: dt.datetime) -> str:
    if value.tzinfo is None:
        raise OperatorError("timestamp must be timezone-aware")
    return value.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso8601(value: str, label: str) -> dt.datetime:
    if not isinstance(value, str) or not value:
        raise OperatorError(f"{label} is missing")
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = dt.datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise OperatorError(f"{label} is not ISO8601") from exc
    if parsed.tzinfo is None:
        raise OperatorError(f"{label} must include a timezone")
    return parsed.astimezone(dt.timezone.utc)


def safe_environment() -> dict[str, str]:
    allowed = {}
    for key in ("HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_RUNTIME_DIR"):
        value = os.environ.get(key)
        if value:
            allowed[key] = value
    allowed["PATH"] = SAFE_PATH
    return allowed


def bk_binary() -> str:
    path = shutil.which("bk", path=SAFE_PATH)
    if not path:
        raise OperatorError("bk CLI is unavailable in the reviewed system PATH")
    return path


def run_bk(args: list[str], *, sensitive_response: bool = False) -> str:
    command = [bk_binary(), *args]
    result = subprocess.run(
        command,
        env=safe_environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        # Never surface stdout/stderr from token create calls: either stream may
        # contain sensitive response material depending on CLI behavior.
        if sensitive_response:
            raise OperatorError(f"Buildkite API request failed with status {result.returncode}")
        detail = result.stderr.strip()
        if len(detail) > 300:
            detail = detail[:300] + "..."
        suffix = f": {detail}" if detail else ""
        raise OperatorError(f"bk command failed with status {result.returncode}{suffix}")
    return result.stdout


def parse_json(raw: str, label: str):
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OperatorError(f"{label} did not return valid JSON") from exc


def preflight(org: str) -> None:
    run_bk(["auth", "switch", org])
    run_bk(["auth", "status", "-o", "json"])
    payload = parse_json(run_bk(["api", "/access-token"]), "bk api /access-token")
    if not isinstance(payload, dict) or not isinstance(payload.get("scopes"), list):
        raise OperatorError("Buildkite access-token response does not contain scopes")
    scopes = {item for item in payload["scopes"] if isinstance(item, str)}
    missing = sorted(REQUIRED_SCOPES - scopes)
    if missing:
        raise OperatorError("Buildkite credential is missing required scopes: " + ", ".join(missing))


def token_endpoint(cluster_id: str, token_id: str | None = None) -> str:
    base = f"/clusters/{cluster_id}/tokens"
    return f"{base}/{token_id}" if token_id else base


def list_tokens(cluster_id: str) -> list[dict]:
    payload = parse_json(run_bk(["api", token_endpoint(cluster_id)]), "agent token list")
    if not isinstance(payload, list):
        raise OperatorError("agent token list returned an unexpected JSON shape")
    if not all(isinstance(item, dict) for item in payload):
        raise OperatorError("agent token list contains a non-object item")
    return payload


def token_is_absent(cluster_id: str, token_id: str) -> bool:
    try:
        remaining = list_tokens(cluster_id)
    except OperatorError:
        return False
    return not any(item.get("id") == token_id for item in remaining)


def secure_parent(path: pathlib.Path) -> None:
    if not path.is_absolute():
        raise OperatorError("--output-file must be an absolute path")
    parent = path.parent
    try:
        st = os.lstat(parent)
    except OSError as exc:
        raise OperatorError("output parent directory is unavailable") from exc
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise OperatorError("output parent must be a real directory, not a symlink")
    if st.st_uid != os.geteuid():
        raise OperatorError("output parent directory must be owned by the current operator")
    if stat.S_IMODE(st.st_mode) & 0o077:
        raise OperatorError("output parent directory must not grant group/other permissions")
    if path.exists() or path.is_symlink():
        raise OperatorError("output token file already exists")


def write_secret_exclusive(path: pathlib.Path, value: str) -> None:
    secure_parent(path)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    try:
        os.write(fd, value.encode("ascii"))
        os.fsync(fd)
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise OperatorError("output token file is not a regular file")
        if st.st_uid != os.geteuid() or stat.S_IMODE(st.st_mode) != 0o600:
            raise OperatorError("output token file ownership or mode is unsafe")
    except Exception:
        try:
            os.close(fd)
        finally:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        raise
    else:
        os.close(fd)


def validate_create_response(
    payload,
    *,
    description: str,
    allowed_cidr: str,
    expires_at: dt.datetime,
) -> tuple[str, str]:
    if not isinstance(payload, dict):
        raise OperatorError("agent token create did not return a JSON object")
    token_id = canonical_uuid(payload.get("id"), "created token id")
    if payload.get("description") != description:
        raise OperatorError("created token description mismatch")
    if payload.get("allowed_ip_addresses") != allowed_cidr:
        raise OperatorError("created token allowed IP mismatch")
    actual_expiry = parse_iso8601(payload.get("expires_at"), "created token expiry")
    if abs((actual_expiry - expires_at).total_seconds()) > 1:
        raise OperatorError("created token expiry mismatch")
    token = payload.get("token")
    if not isinstance(token, str) or not token.startswith("bkct_") or token.strip() != token or len(token) <= 5:
        raise OperatorError("created token value has an unexpected format")
    return token_id, token


def revoke_created_token(cluster_id: str, token_id: str) -> bool:
    try:
        run_bk(["api", "--method", "DELETE", token_endpoint(cluster_id, token_id)], sensitive_response=True)
    except OperatorError:
        return False
    return token_is_absent(cluster_id, token_id)


def create_token(args: argparse.Namespace) -> int:
    if not ORG_RE.fullmatch(args.org):
        raise OperatorError("invalid --org")
    cluster_id = canonical_uuid(args.cluster_id, "--cluster-id")
    if not WORKER_RE.fullmatch(args.worker_id):
        raise OperatorError("invalid --worker-id")
    allowed_cidr = exact_ipv4_cidr(args.allowed_ip)
    if args.ttl_minutes < MIN_TTL_MINUTES or args.ttl_minutes > MAX_TTL_MINUTES:
        raise OperatorError(f"--ttl-minutes must be between {MIN_TTL_MINUTES} and {MAX_TTL_MINUTES}")
    output = pathlib.Path(args.output_file)
    description = DESCRIPTION_PREFIX + args.worker_id

    if not args.apply:
        print("HostPanel Buildkite per-worker agent-token plan")
        print(f"organization={args.org}")
        print(f"cluster_id={cluster_id}")
        print(f"worker_id={args.worker_id}")
        print(f"allowed_ip_addresses={allowed_cidr}")
        print(f"ttl_minutes={args.ttl_minutes}")
        print(f"output_file={output}")
        print("No Buildkite API writes are performed in plan mode.")
        print("Apply mode never prints the token value.")
        return 0

    if not args.confirm_create:
        raise OperatorError("create --apply requires --confirm-create")

    secure_parent(output)
    preflight(args.org)

    for item in list_tokens(cluster_id):
        if item.get("description") == description:
            raise OperatorError("an agent token already exists for this worker id; inspect/revoke it instead of creating a duplicate")

    expires = dt.datetime.now(dt.timezone.utc).replace(microsecond=0) + dt.timedelta(minutes=args.ttl_minutes)
    payload = json.dumps(
        {
            "description": description,
            "expires_at": isoformat_z(expires),
            "allowed_ip_addresses": allowed_cidr,
        },
        separators=(",", ":"),
    )
    raw = run_bk(
        ["api", "--method", "POST", token_endpoint(cluster_id), "--data", payload],
        sensitive_response=True,
    )
    token_id = ""
    try:
        created = parse_json(raw, "agent token create")
        if isinstance(created, dict) and isinstance(created.get("id"), str):
            token_id = canonical_uuid(created["id"], "created token id")
        token_id, token = validate_create_response(
            created,
            description=description,
            allowed_cidr=allowed_cidr,
            expires_at=expires,
        )
        write_secret_exclusive(output, token)
    except Exception as exc:
        if not token_id:
            try:
                matches = [
                    item for item in list_tokens(cluster_id)
                    if item.get("description") == description and isinstance(item.get("id"), str)
                ]
                if len(matches) == 1:
                    token_id = canonical_uuid(matches[0]["id"], "recovered token id")
            except OperatorError:
                token_id = ""
        if token_id:
            revoked = revoke_created_token(cluster_id, token_id)
            if not revoked:
                raise OperatorError(
                    f"token create follow-up failed and automatic revocation/absence verification also failed; revoke token id {token_id} manually"
                ) from exc
        raise

    print("Created short-lived Buildkite agent token without printing its value.")
    print(f"token_id={token_id}")
    print(f"worker_id={args.worker_id}")
    print(f"allowed_ip_addresses={allowed_cidr}")
    print(f"expires_at={isoformat_z(expires)}")
    print(f"token_file={output}")
    print("After the worker connects and smoke-test passes, revoke this token immediately.")
    return 0


def safe_remove_token_file(path: pathlib.Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    st = os.lstat(path)
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise OperatorError("refusing to remove a non-regular or symlink token file")
    if st.st_uid != os.geteuid() or stat.S_IMODE(st.st_mode) != 0o600:
        raise OperatorError("refusing to remove token file with unsafe ownership or mode")
    path.unlink()


def revoke_token(args: argparse.Namespace) -> int:
    if not ORG_RE.fullmatch(args.org):
        raise OperatorError("invalid --org")
    cluster_id = canonical_uuid(args.cluster_id, "--cluster-id")
    token_id = canonical_uuid(args.token_id, "--token-id")
    token_file = pathlib.Path(args.token_file) if args.token_file else None

    if not args.apply:
        print("HostPanel Buildkite agent-token revocation plan")
        print(f"organization={args.org}")
        print(f"cluster_id={cluster_id}")
        print(f"token_id={token_id}")
        if token_file:
            print(f"token_file={token_file}")
        print("No Buildkite API writes are performed in plan mode.")
        return 0

    if not args.confirm_revoke:
        raise OperatorError("revoke --apply requires --confirm-revoke")

    preflight(args.org)
    raw = run_bk(["api", token_endpoint(cluster_id, token_id)])
    payload = parse_json(raw, "agent token lookup")
    if not isinstance(payload, dict):
        raise OperatorError("agent token lookup did not return a JSON object")
    if canonical_uuid(payload.get("id"), "looked-up token id") != token_id:
        raise OperatorError("looked-up token id mismatch")
    description = payload.get("description")
    if not isinstance(description, str) or not description.startswith(DESCRIPTION_PREFIX):
        raise OperatorError("refusing to revoke a token not created for a HostPanel per-worker registration")

    run_bk(["api", "--method", "DELETE", token_endpoint(cluster_id, token_id)], sensitive_response=True)
    if not token_is_absent(cluster_id, token_id):
        raise OperatorError("revoked token id is still present or its absence could not be verified")
    if token_file:
        safe_remove_token_file(token_file)

    print(f"Revoked and verified removal of HostPanel per-worker Buildkite agent token {token_id}.")
    if token_file:
        print("Removed the local token file if it was still present.")
    return 0


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(description="Issue and revoke short-lived HostPanel Buildkite cluster agent tokens.")
    sub = top.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create")
    create.add_argument("--org", required=True)
    create.add_argument("--cluster-id", required=True)
    create.add_argument("--worker-id", required=True)
    create.add_argument("--allowed-ip", required=True)
    create.add_argument("--output-file", required=True)
    create.add_argument("--ttl-minutes", type=int, default=15)
    create.add_argument("--apply", action="store_true")
    create.add_argument("--confirm-create", action="store_true")

    revoke = sub.add_parser("revoke")
    revoke.add_argument("--org", required=True)
    revoke.add_argument("--cluster-id", required=True)
    revoke.add_argument("--token-id", required=True)
    revoke.add_argument("--token-file")
    revoke.add_argument("--apply", action="store_true")
    revoke.add_argument("--confirm-revoke", action="store_true")
    return top


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "create":
            return create_token(args)
        if args.command == "revoke":
            return revoke_token(args)
        raise OperatorError("unsupported command")
    except OperatorError as exc:
        print(f"HostPanel Buildkite agent-token operator failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
