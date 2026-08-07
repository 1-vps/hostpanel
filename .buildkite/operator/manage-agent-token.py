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
EXPECTED_CLUSTER_NAME = "HostPanel"
EXPECTED_QUEUE_KEYS = frozenset({"hostpanel-upload", "hostpanel-ci", "hostpanel-qemu"})
MIN_TTL_MINUTES = 15
MAX_TTL_MINUTES = 60
MAX_INVENTORY_ITEMS = 100


class OperatorError(RuntimeError):
    pass


def canonical_uuid(value: str, label: str) -> str:
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError, TypeError) as exc:
        raise OperatorError(f"{label} must be a canonical UUID") from exc
    canonical = str(parsed)
    if not isinstance(value, str) or value.lower() != canonical:
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


def require_apply_root() -> None:
    if os.geteuid() != 0:
        raise OperatorError("apply mode must run as root on the trusted operator host")


def trusted_home() -> str:
    home = pathlib.Path("/root")
    try:
        info = os.lstat(home)
    except OSError as exc:
        raise OperatorError("trusted root HOME /root is unavailable") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise OperatorError("trusted root HOME /root must be a real directory")
    if info.st_uid != 0 or stat.S_IMODE(info.st_mode) & 0o022:
        raise OperatorError("trusted root HOME /root ownership or mode is unsafe")
    return str(home)


def safe_environment() -> dict[str, str]:
    allowed = {
        "PATH": SAFE_PATH,
        "HOME": trusted_home(),
        "USER": "root",
        "LOGNAME": "root",
    }
    for key in ("LANG", "LC_ALL"):
        value = os.environ.get(key)
        if value:
            allowed[key] = value
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


def queue_endpoint(cluster_id: str) -> str:
    return f"/clusters/{cluster_id}/queues?per_page={MAX_INVENTORY_ITEMS}"


def verify_hostpanel_cluster(cluster_id: str) -> None:
    cluster = parse_json(
        run_bk(["api", f"/clusters/{cluster_id}"]),
        "HostPanel cluster lookup",
    )
    if not isinstance(cluster, dict):
        raise OperatorError("HostPanel cluster lookup returned an unexpected JSON shape")
    if canonical_uuid(cluster.get("id"), "looked-up cluster id") != cluster_id:
        raise OperatorError("looked-up cluster id mismatch")
    if cluster.get("name") != EXPECTED_CLUSTER_NAME:
        raise OperatorError("refusing to manage worker tokens outside the HostPanel cluster")
    if "default_queue_id" not in cluster or cluster["default_queue_id"] is not None:
        raise OperatorError("HostPanel cluster must explicitly have no default queue")

    queues = parse_json(run_bk(["api", queue_endpoint(cluster_id)]), "HostPanel queue inventory")
    if not isinstance(queues, list) or not all(isinstance(item, dict) for item in queues):
        raise OperatorError("HostPanel queue inventory returned an unexpected JSON shape")
    if len(queues) >= MAX_INVENTORY_ITEMS:
        raise OperatorError(
            "HostPanel queue inventory reached the 100-item pagination bound; "
            "cannot prove inventory completeness"
        )
    if len(queues) != len(EXPECTED_QUEUE_KEYS):
        raise OperatorError("HostPanel cluster must contain exactly the three reviewed queues")

    seen: set[str] = set()
    for item in queues:
        key = item.get("key")
        if not isinstance(key, str) or key not in EXPECTED_QUEUE_KEYS or key in seen:
            raise OperatorError("HostPanel queue inventory contains an unreviewed or duplicate key")
        seen.add(key)
        canonical_uuid(item.get("id"), f"{key} queue id")
        if item.get("dispatch_paused") is not False:
            raise OperatorError(f"HostPanel queue {key} must explicitly have dispatch_paused=false")
        if item.get("hosted") not in (None, False) or item.get("hosted_agents") is not None:
            raise OperatorError(f"HostPanel queue {key} must be self-hosted")
    if seen != EXPECTED_QUEUE_KEYS:
        raise OperatorError("HostPanel cluster queue keys do not match the reviewed set")


def list_tokens(cluster_id: str) -> list[dict]:
    endpoint = token_endpoint(cluster_id) + f"?per_page={MAX_INVENTORY_ITEMS}"
    payload = parse_json(run_bk(["api", endpoint]), "agent token list")
    if not isinstance(payload, list):
        raise OperatorError("agent token list returned an unexpected JSON shape")
    if not all(isinstance(item, dict) for item in payload):
        raise OperatorError("agent token list contains a non-object item")
    if len(payload) >= MAX_INVENTORY_ITEMS:
        raise OperatorError(
            "agent token inventory reached the 100-item pagination bound; "
            "cannot prove inventory completeness"
        )
    return payload


def token_is_absent(cluster_id: str, token_id: str) -> bool:
    try:
        remaining = list_tokens(cluster_id)
    except OperatorError:
        return False
    return not any(item.get("id") == token_id for item in remaining)


def fd_filesystem_type(fd: int) -> str:
    mount_id = None
    try:
        with open(f"/proc/self/fdinfo/{fd}", "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("mnt_id:"):
                    mount_id = line.split(":", 1)[1].strip()
                    break
    except OSError as exc:
        raise OperatorError("cannot inspect token directory mount identity") from exc
    if not mount_id:
        raise OperatorError("cannot determine token directory mount identity")

    try:
        with open("/proc/self/mountinfo", "r", encoding="utf-8") as handle:
            for line in handle:
                left, separator, right = line.rstrip("\n").partition(" - ")
                if not separator:
                    continue
                fields = left.split()
                if fields and fields[0] == mount_id:
                    right_fields = right.split()
                    if right_fields:
                        return right_fields[0]
    except OSError as exc:
        raise OperatorError("cannot inspect token directory filesystem") from exc
    raise OperatorError("cannot resolve token directory filesystem type")


def open_secure_parent(path: pathlib.Path) -> int:
    if not path.is_absolute():
        raise OperatorError("token file path must be absolute")
    if path.name in {"", ".", ".."}:
        raise OperatorError("token file path has an unsafe basename")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_DIRECTORY", 0)
    try:
        fd = os.open(path.parent, flags)
    except OSError as exc:
        raise OperatorError("token parent directory is unavailable or unsafe") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISDIR(info.st_mode):
            raise OperatorError("token parent must be a directory")
        if info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o700:
            raise OperatorError("token parent must be root-owned mode 0700")
        if fd_filesystem_type(fd) != "tmpfs":
            raise OperatorError("token parent directory must reside on tmpfs")
        return fd
    except Exception:
        os.close(fd)
        raise


def secure_parent(path: pathlib.Path) -> None:
    parent_fd = open_secure_parent(path)
    try:
        try:
            os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        raise OperatorError("output token file already exists")
    finally:
        os.close(parent_fd)


def write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OperatorError("short write while storing Buildkite agent token")
        view = view[written:]


def write_secret_exclusive(path: pathlib.Path, value: str) -> None:
    data = value.encode("ascii")
    parent_fd = open_secure_parent(path)
    fd = -1
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(path.name, flags, 0o600, dir_fd=parent_fd)
        except FileExistsError as exc:
            raise OperatorError("output token file already exists") from exc

        write_all(fd, data)
        os.fsync(fd)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise OperatorError("output token file is not a single regular file")
        if info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600:
            raise OperatorError("output token file ownership or mode is unsafe")
        if info.st_size != len(data):
            raise OperatorError("output token file length does not match the secret")
    except Exception:
        if fd >= 0:
            os.close(fd)
            fd = -1
        try:
            os.unlink(path.name, dir_fd=parent_fd)
        except FileNotFoundError:
            pass
        raise
    finally:
        if fd >= 0:
            os.close(fd)
        os.close(parent_fd)


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
    if (
        not isinstance(token, str)
        or token.strip() != token
        or not token.isascii()
        or any(ch.isspace() for ch in token)
        or not (20 <= len(token) <= 512)
    ):
        raise OperatorError("created token value has an unexpected opaque format")
    return token_id, token


def revoke_created_token(cluster_id: str, token_id: str) -> bool:
    try:
        run_bk(
            ["api", "--method", "DELETE", token_endpoint(cluster_id, token_id)],
            sensitive_response=True,
        )
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
        raise OperatorError(
            f"--ttl-minutes must be between {MIN_TTL_MINUTES} and {MAX_TTL_MINUTES}"
        )
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
        print("Apply mode requires root and stores the token only in root-owned tmpfs.")
        print("Apply mode never prints the token value.")
        return 0

    if not args.confirm_create:
        raise OperatorError("create --apply requires --confirm-create")

    require_apply_root()
    secure_parent(output)
    preflight(args.org)
    verify_hostpanel_cluster(cluster_id)

    for item in list_tokens(cluster_id):
        if item.get("description") == description:
            raise OperatorError(
                "an agent token already exists for this worker id; "
                "inspect/revoke it instead of creating a duplicate"
            )

    expires = (
        dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        + dt.timedelta(minutes=args.ttl_minutes)
    )
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
                    item
                    for item in list_tokens(cluster_id)
                    if item.get("description") == description
                    and isinstance(item.get("id"), str)
                ]
                if len(matches) == 1:
                    token_id = canonical_uuid(matches[0]["id"], "recovered token id")
            except OperatorError:
                token_id = ""
        if token_id:
            revoked = revoke_created_token(cluster_id, token_id)
            if not revoked:
                raise OperatorError(
                    "token create follow-up failed and automatic "
                    "revocation/absence verification also failed; "
                    f"revoke token id {token_id} manually"
                ) from exc
            raise
        raise OperatorError(
            "token create returned success but its token id could not be safely recovered; "
            f"inspect cluster tokens for description {description!r} and revoke any match "
            "before provisioning"
        ) from exc

    print("Created short-lived Buildkite agent token without printing its value.")
    print(f"token_id={token_id}")
    print(f"worker_id={args.worker_id}")
    print(f"allowed_ip_addresses={allowed_cidr}")
    print(f"expires_at={isoformat_z(expires)}")
    print(f"token_file={output}")
    print("After the worker connects and smoke-test passes, revoke this token immediately.")
    return 0


def safe_remove_token_file(path: pathlib.Path) -> None:
    parent_fd = open_secure_parent(path)
    try:
        try:
            info = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise OperatorError("refusing to remove a non-regular token file")
        if info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600:
            raise OperatorError("refusing to remove token file with unsafe ownership or mode")
        os.unlink(path.name, dir_fd=parent_fd)
        try:
            os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        raise OperatorError("local token file still exists after unlink")
    finally:
        os.close(parent_fd)


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

    require_apply_root()
    if token_file:
        parent_fd = open_secure_parent(token_file)
        os.close(parent_fd)
    preflight(args.org)
    verify_hostpanel_cluster(cluster_id)

    raw = run_bk(["api", token_endpoint(cluster_id, token_id)])
    payload = parse_json(raw, "agent token lookup")
    if not isinstance(payload, dict):
        raise OperatorError("agent token lookup did not return a JSON object")
    if canonical_uuid(payload.get("id"), "looked-up token id") != token_id:
        raise OperatorError("looked-up token id mismatch")
    description = payload.get("description")
    if not isinstance(description, str) or not description.startswith(DESCRIPTION_PREFIX):
        raise OperatorError(
            "refusing to revoke a token not created for a HostPanel per-worker registration"
        )

    run_bk(
        ["api", "--method", "DELETE", token_endpoint(cluster_id, token_id)],
        sensitive_response=True,
    )
    if not token_is_absent(cluster_id, token_id):
        raise OperatorError(
            "revoked token id is still present or its absence could not be verified"
        )
    if token_file:
        safe_remove_token_file(token_file)

    print(
        f"Revoked and verified removal of HostPanel per-worker Buildkite agent token {token_id}."
    )
    if token_file:
        print("Removed the local token file if it was still present.")
    return 0


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(
        description="Issue and revoke short-lived HostPanel Buildkite cluster agent tokens."
    )
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
