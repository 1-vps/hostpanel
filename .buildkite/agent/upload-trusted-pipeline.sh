#!/usr/bin/env bash
set -euo pipefail

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
unset BASH_ENV ENV PYTHONPATH PYTHONHOME LD_PRELOAD LD_LIBRARY_PATH \
  GIT_CONFIG_GLOBAL GIT_CONFIG_SYSTEM GIT_DIR GIT_WORK_TREE SSH_AUTH_SOCK CDPATH

(($# == 0)) || {
  printf 'HostPanel trusted pipeline upload failed: positional arguments are forbidden\n' >&2
  exit 1
}

pipeline=/etc/buildkite-agent/hostpanel-pipeline.yml
digest_file=/etc/buildkite-agent/hostpanel-policy/pipeline-sha256

python3 - "$pipeline" "$digest_file" <<'PY'
import hashlib
import os
import pathlib
import re
import stat
import sys


def read_root_owned(path_value: str, maximum: int) -> bytes:
    path = pathlib.Path(path_value)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise SystemExit(f"unsafe trusted pipeline input: {path}")
        if info.st_uid != 0 or info.st_mode & 0o022:
            raise SystemExit(f"trusted pipeline input is not root-controlled: {path}")
        if info.st_size <= 0 or info.st_size > maximum:
            raise SystemExit(f"trusted pipeline input has unsafe size: {path}")
        data = os.read(descriptor, maximum + 1)
        after = os.fstat(descriptor)
        if len(data) != info.st_size or len(data) > maximum:
            raise SystemExit(f"trusted pipeline input changed while read: {path}")
        if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise SystemExit(f"trusted pipeline input changed while read: {path}")
        return data
    finally:
        os.close(descriptor)


pipeline_data = read_root_owned(sys.argv[1], 1024 * 1024)
digest = read_root_owned(sys.argv[2], 256).decode("ascii", errors="strict").strip()
if not re.fullmatch(r"[0-9a-f]{64}", digest):
    raise SystemExit("trusted pipeline digest is malformed")
actual = hashlib.sha256(pipeline_data).hexdigest()
if actual != digest:
    raise SystemExit("trusted pipeline digest mismatch")
PY

command -v buildkite-agent >/dev/null 2>&1 || {
  printf 'HostPanel trusted pipeline upload failed: buildkite-agent is unavailable\n' >&2
  exit 1
}

buildkite-agent pipeline upload \
  --no-interpolation \
  --reject-secrets \
  --reject-parse-warnings \
  "$pipeline"
