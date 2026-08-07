#!/usr/bin/env bash
set -euo pipefail

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
unset BASH_ENV ENV PYTHONPATH PYTHONHOME LD_PRELOAD LD_LIBRARY_PATH
umask 077

fail() {
  printf 'HostPanel Buildkite checkout-key generation failed: %s\n' "$*" >&2
  exit 1
}

(($# == 1)) || fail "usage: $0 OUTPUT_DIRECTORY"
out_dir="$1"
command -v ssh-keygen >/dev/null 2>&1 || fail "ssh-keygen is unavailable"
command -v python3 >/dev/null 2>&1 || fail "python3 is unavailable"

[[ ! -e "$out_dir" && ! -L "$out_dir" ]] || fail "output path already exists"
install -d -m 0700 "$out_dir"
out_dir="$(cd "$out_dir" && pwd -P)"
private_path="$out_dir/checkout-deploy-key"
public_path="$out_dir/checkout-deploy-key.pub"

ssh-keygen -q -t ed25519 -N "" -C "hostpanel-buildkite-read-only" -f "$private_path"
chmod 0600 "$private_path"
chmod 0644 "$public_path"

python3 - "$private_path" "$public_path" <<'PY'
import pathlib, stat, sys
private_path = pathlib.Path(sys.argv[1])
public_path = pathlib.Path(sys.argv[2])
for path, expected_mode, limit in (
    (private_path, 0o600, 16 * 1024),
    (public_path, 0o644, 4 * 1024),
):
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise SystemExit(f"unsafe generated file: {path}")
    if stat.S_IMODE(info.st_mode) != expected_mode:
        raise SystemExit(f"unsafe generated mode: {path}")
    if info.st_size <= 0 or info.st_size > limit:
        raise SystemExit(f"unsafe generated size: {path}")
private = private_path.read_text(encoding="utf-8")
public = public_path.read_text(encoding="utf-8").strip()
if not private.startswith("-----BEGIN OPENSSH PRIVATE KEY-----\n"):
    raise SystemExit("generated private key is not OpenSSH format")
parts = public.split()
if len(parts) < 2 or parts[0] != "ssh-ed25519":
    raise SystemExit("generated public key is not Ed25519")
PY

printf 'Generated private checkout key: %s\n' "$private_path"
printf 'Generated public deploy key: %s\n' "$public_path"
printf 'Add the public key to GitHub as a read-only deploy key.\n'
printf 'Stage the private key only as a root-owned 0600 file on tmpfs (for example under /run) on each fresh CI/QEMU worker; keep swap disabled; the trusted checkout hook destroys its installed copy before repository code runs.\n'
printf 'Never store this private key in Buildkite Secrets or commit it.\n'
