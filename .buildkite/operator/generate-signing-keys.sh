#!/usr/bin/env bash
set -euo pipefail

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
unset BASH_ENV ENV PYTHONPATH PYTHONHOME LD_PRELOAD LD_LIBRARY_PATH
umask 077

fail() {
  printf 'HostPanel Buildkite key generation failed: %s\n' "$*" >&2
  exit 1
}

(($# == 2)) || fail "usage: $0 OUTPUT_DIRECTORY KEY_ID"
out_dir="$1"
key_id="$2"
[[ "$key_id" =~ ^[A-Za-z0-9._:-]{1,128}$ ]] || fail "KEY_ID has an unsafe format"
command -v buildkite-agent >/dev/null 2>&1 || fail "buildkite-agent is unavailable"
command -v python3 >/dev/null 2>&1 || fail "python3 is unavailable"

[[ ! -e "$out_dir" && ! -L "$out_dir" ]] || fail "output path already exists"
install -d -m 0700 "$out_dir"
out_dir="$(cd "$out_dir" && pwd -P)"
private_path="$out_dir/signing-private.jwks"
public_path="$out_dir/verification-public.jwks"

buildkite-agent tool keygen \
  --alg EdDSA \
  --key-id "$key_id" \
  --private-jwks-file "$private_path" \
  --public-jwks-file "$public_path"
chmod 0600 "$private_path"
chmod 0644 "$public_path"

python3 - "$private_path" "$public_path" "$key_id" <<'PY'
import json
import pathlib
import sys

private_path = pathlib.Path(sys.argv[1])
public_path = pathlib.Path(sys.argv[2])
key_id = sys.argv[3]
private = json.loads(private_path.read_text(encoding="utf-8"))
public = json.loads(public_path.read_text(encoding="utf-8"))
for label, value in (("private", private), ("public", public)):
    if not isinstance(value, dict) or not isinstance(value.get("keys"), list):
        raise SystemExit(f"{label} output is not a JWKS")
    if len(value["keys"]) != 1 or not isinstance(value["keys"][0], dict):
        raise SystemExit(f"{label} JWKS must contain exactly one key")
private_key = private["keys"][0]
public_key = public["keys"][0]
if private_key.get("kid") != key_id or public_key.get("kid") != key_id:
    raise SystemExit("generated key ID does not match the request")
if "d" not in private_key:
    raise SystemExit("private JWKS lacks private key material")
if "d" in public_key:
    raise SystemExit("public JWKS contains private key material")
for field in ("kty", "crv", "x", "kid"):
    if private_key.get(field) != public_key.get(field):
        raise SystemExit(f"public/private key mismatch in {field}")
PY

printf 'Generated private signing JWKS: %s\n' "$private_path"
printf 'Generated public verification JWKS: %s\n' "$public_path"
printf 'Keep the private file off runner queues and outside the repository.\n'
