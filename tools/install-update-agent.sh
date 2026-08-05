#!/usr/bin/env bash
# Install or refresh the signed GitHub release update agent.
set -Eeuo pipefail

PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH
umask 077
unset PYTHONPATH PYTHONHOME BASH_ENV ENV LD_PRELOAD LD_LIBRARY_PATH

[[ ${EUID:-$(id -u)} -eq 0 ]] || {
  echo 'Error: install-update-agent.sh must run as root.' >&2
  exit 1
}

SOURCE_ROOT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
UPDATER_IMPL="$SOURCE_ROOT/tools/hostpanel-update.py"
SERVICE="$SOURCE_ROOT/packaging/systemd/hostpanel-update.service"
TIMER="$SOURCE_ROOT/packaging/systemd/hostpanel-update.timer"
KEYRING="$SOURCE_ROOT/releases/update-keyring.json"

for path in "$UPDATER_IMPL" "$SERVICE" "$TIMER" "$KEYRING"; do
  [[ -f "$path" && ! -L "$path" ]] || {
    echo "Error: unsafe or missing update-agent input: $path" >&2
    exit 1
  }
done

mapfile -t KEY_FILES < <(
  python3 - "$KEYRING" <<'PY'
import json
import pathlib
import re
import sys

path = pathlib.Path(sys.argv[1])
data = json.loads(path.read_text(encoding="utf-8"))
if not isinstance(data, dict) or set(data) != {"schema", "keys"} or data["schema"] != 1:
    raise SystemExit("unsafe update keyring shape")
entries = data["keys"]
if not isinstance(entries, list) or not entries or len(entries) > 8:
    raise SystemExit("unsafe update key count")
seen = set()
for entry in entries:
    if not isinstance(entry, dict) or set(entry) != {
        "id", "file", "activate_from", "retire_after"
    }:
        raise SystemExit("unsafe update keyring entry")
    name = entry["file"]
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", name):
        raise SystemExit("unsafe update public-key filename")
    if name in seen:
        raise SystemExit("duplicate update public-key filename")
    seen.add(name)
    print(name)
PY
)

for name in "${KEY_FILES[@]}"; do
  path="$SOURCE_ROOT/releases/$name"
  [[ -f "$path" && ! -L "$path" ]] || {
    echo "Error: unsafe or missing update public key: $path" >&2
    exit 1
  }
done

install -d -o root -g root -m 755 /opt/hostpanel/tools
install -d -o root -g root -m 700 /etc/hostpanel /var/lib/hostpanel
install -o root -g root -m 644 \
  "$UPDATER_IMPL" /opt/hostpanel/tools/hostpanel-update-impl.py
python3 - /opt/hostpanel/tools/hostpanel-update <<'PYENTRY'
import os
import pathlib
import secrets
import sys

ENTRY_PAYLOAD = r'''#!/usr/bin/env python3
"""Hardened runtime entrypoint for the signed HostPanel updater."""
from __future__ import annotations

import contextlib
import fcntl
import importlib.util
import json
import os
import pathlib
import secrets
import stat
import sys

_IMPL_PATH = pathlib.Path(__file__).with_name('hostpanel-update-impl.py')
_SPEC = importlib.util.spec_from_file_location(
    '_hostpanel_update_impl', _IMPL_PATH
)
if _SPEC is None or _SPEC.loader is None:
    raise SystemExit(f'cannot load HostPanel updater implementation: {_IMPL_PATH}')
_IMPL = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _IMPL
_SPEC.loader.exec_module(_IMPL)

UpdateError = _IMPL.UpdateError
_RAW_OS = os
_RAW_OS_WRITE = os.write


def _write_all(fd: int, payload) -> int:
    view = memoryview(payload)
    total = len(view)
    while view:
        written = _RAW_OS_WRITE(fd, view)
        if written <= 0:
            raise UpdateError('could not complete updater file write')
        view = view[written:]
    return total


class _UpdaterOsProxy:
    def __getattr__(self, name: str):
        return getattr(_RAW_OS, name)

    write = staticmethod(_write_all)


_UPDATER_OS = _UpdaterOsProxy()
# The preserved updater uses its module-global `os` reference for release
# downloads, archive extraction, and status publication. Replace only that
# reference; do not mutate the process-global os module used by other code.
_IMPL.os = _UPDATER_OS


def _capture_xattrs(path: pathlib.Path) -> dict[str, bytes]:
    required = ('listxattr', 'getxattr', 'setxattr', 'removexattr')
    if not all(hasattr(os, name) for name in required):
        raise UpdateError('extended-attribute support is unavailable')
    try:
        return {
            name: os.getxattr(path, name, follow_symlinks=False)
            for name in os.listxattr(path, follow_symlinks=False)
        }
    except OSError as exc:
        raise UpdateError(
            f'could not capture update-status metadata for {path}: {exc}'
        ) from exc


def _apply_xattrs(path: pathlib.Path, values: dict[str, bytes]) -> None:
    try:
        existing = set(os.listxattr(path, follow_symlinks=False))
        desired = set(values)
        for name in existing - desired:
            os.removexattr(path, name, follow_symlinks=False)
        for name, value in values.items():
            os.setxattr(path, name, value, follow_symlinks=False)
    except OSError as exc:
        raise UpdateError(
            f'could not restore update-status metadata for {path}: {exc}'
        ) from exc


def _open_temporary(path: pathlib.Path) -> tuple[int, pathlib.Path]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    for _ in range(32):
        temporary = path.with_name(
            f'.{path.name}.hostpanel-update.{secrets.token_hex(12)}'
        )
        try:
            return os.open(temporary, flags, 0o600), temporary
        except FileExistsError:
            continue
    raise UpdateError(f'could not allocate status temporary for {path}')


def _fsync_parent(path: pathlib.Path) -> None:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, 'O_DIRECTORY'):
        flags |= os.O_DIRECTORY
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    try:
        directory_fd = os.open(path.parent, flags)
    except OSError as exc:
        raise UpdateError(f'could not open status directory: {path.parent}') from exc
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _validate_status_target(
    path: pathlib.Path, owner_uid: int, owner_gid: int
) -> dict[str, bytes] | None:
    if not os.path.lexists(path):
        return None
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != owner_uid
        or metadata.st_gid != owner_gid
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise UpdateError(f'unsafe update-status file: {path}')
    return _capture_xattrs(path)


def atomic_json(path: pathlib.Path, payload: dict[str, object]) -> None:
    owner_uid, owner_gid = _IMPL._owner_ids()
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as exc:
        raise UpdateError(
            f'could not create update-status directory: {path.parent}'
        ) from exc
    _IMPL._validate_direct_parent(path, owner_uid, owner_gid)
    existing_xattrs = _validate_status_target(path, owner_uid, owner_gid)
    encoded = (
        json.dumps(payload, sort_keys=True, indent=2) + '\n'
    ).encode('utf-8')

    fd, temporary = _open_temporary(path)
    active_error: BaseException | None = None
    cleanup_error: BaseException | None = None
    try:
        view = memoryview(encoded)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise UpdateError(f'could not write update status: {temporary}')
            view = view[written:]
        os.fsync(fd)
        os.fchown(fd, owner_uid, owner_gid)
        os.fchmod(fd, 0o600)
        if existing_xattrs is not None:
            _apply_xattrs(temporary, existing_xattrs)
        os.fsync(fd)
        descriptor, fd = fd, -1
        os.close(descriptor)
        os.replace(temporary, path)
        _fsync_parent(path)
    except BaseException as exc:
        active_error = exc
        raise
    finally:
        if fd >= 0:
            descriptor, fd = fd, -1
            try:
                os.close(descriptor)
            except BaseException as exc:
                cleanup_error = exc
        try:
            temporary.unlink(missing_ok=True)
        except BaseException as exc:
            if cleanup_error is None:
                cleanup_error = exc
        if active_error is None and cleanup_error is not None:
            raise cleanup_error


@contextlib.contextmanager
def safe_lock(path: pathlib.Path):
    owner_uid, owner_gid = _IMPL._owner_ids()
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as exc:
        raise UpdateError(
            f'could not create updater lock directory: {path.parent}'
        ) from exc
    _IMPL._validate_direct_parent(path, owner_uid, owner_gid)

    before = None
    if os.path.lexists(path):
        try:
            before = path.lstat()
        except OSError as exc:
            raise UpdateError(f'could not inspect updater lock: {path}') from exc
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_ISLNK(before.st_mode)
            or before.st_uid != owner_uid
            or before.st_gid != owner_gid
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != 0o600
        ):
            raise UpdateError(f'unsafe updater lock file: {path}')

    flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as exc:
        raise UpdateError(f'could not safely open updater lock: {path}') from exc
    lock = None
    try:
        opened = os.fstat(fd)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != owner_uid
            or opened.st_gid != owner_gid
            or opened.st_nlink != 1
        ):
            raise UpdateError(f'unsafe updater lock file: {path}')
        if before is not None and (
            before.st_dev, before.st_ino
        ) != (opened.st_dev, opened.st_ino):
            raise UpdateError(f'updater lock changed before open: {path}')
        os.fchmod(fd, 0o600)
        try:
            current = path.lstat()
        except OSError as exc:
            raise UpdateError(f'updater lock path changed: {path}') from exc
        if (
            current.st_dev, current.st_ino
        ) != (opened.st_dev, opened.st_ino):
            raise UpdateError(f'updater lock path changed: {path}')
        lock = os.fdopen(fd, 'a+b')
        fd = -1
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise
        yield lock
    finally:
        if lock is not None:
            lock.close()
        elif fd >= 0:
            os.close(fd)


_IMPL.atomic_json = atomic_json


def main(argv: list[str] | None = None) -> int:
    _IMPL.os = _UPDATER_OS
    _IMPL.atomic_json = atomic_json
    args = _IMPL.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        with safe_lock(pathlib.Path(args.lock_file)):
            return _IMPL.run(args)
    except BlockingIOError:
        print('Another HostPanel update is already running.', file=sys.stderr)
        return 75
    except UpdateError as exc:
        with contextlib.suppress(Exception):
            _IMPL.record_status(
                pathlib.Path(args.status_file),
                state='error',
                message=str(exc),
            )
        print(f'HostPanel update failed: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
'''

destination = pathlib.Path(sys.argv[1])
parent = destination.parent
metadata = parent.lstat()
if not parent.is_dir() or parent.is_symlink() or metadata.st_uid != 0 or metadata.st_gid != 0:
    raise SystemExit(f'unsafe updater destination directory: {parent}')
flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
if hasattr(os, 'O_NOFOLLOW'):
    flags |= os.O_NOFOLLOW
for _ in range(32):
    temporary = destination.with_name(
        f'.{destination.name}.install.{secrets.token_hex(12)}'
    )
    try:
        fd = os.open(temporary, flags, 0o755)
        break
    except FileExistsError:
        continue
else:
    raise SystemExit('could not allocate updater entry temporary')
active_error = None
cleanup_error = None
try:
    view = memoryview(ENTRY_PAYLOAD.encode('utf-8'))
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise SystemExit('could not write updater entrypoint')
        view = view[written:]
    os.fsync(fd)
    os.fchown(fd, 0, 0)
    os.fchmod(fd, 0o755)
    os.fsync(fd)
    descriptor, fd = fd, -1
    os.close(descriptor)
    os.replace(temporary, destination)
    directory_fd = os.open(
        parent,
        os.O_RDONLY | os.O_CLOEXEC
        | getattr(os, 'O_DIRECTORY', 0)
        | getattr(os, 'O_NOFOLLOW', 0),
    )
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
except BaseException as exc:
    active_error = exc
    raise
finally:
    if fd >= 0:
        descriptor, fd = fd, -1
        try:
            os.close(descriptor)
        except BaseException as exc:
            cleanup_error = exc
    try:
        temporary.unlink(missing_ok=True)
    except BaseException as exc:
        if cleanup_error is None:
            cleanup_error = exc
    if active_error is None and cleanup_error is not None:
        raise cleanup_error
PYENTRY
for name in "${KEY_FILES[@]}"; do
  install -o root -g root -m 644 \
    "$SOURCE_ROOT/releases/$name" "/etc/hostpanel/$name"
done
install -o root -g root -m 600 "$KEYRING" /etc/hostpanel/update-keyring.json
install -o root -g root -m 644 "$SERVICE" /etc/systemd/system/hostpanel-update.service
install -o root -g root -m 644 "$TIMER" /etc/systemd/system/hostpanel-update.timer

CONFIG=/etc/hostpanel/update-agent.conf
if [[ -L "$CONFIG" ]]; then
  echo "Error: unsafe update-agent configuration symlink: $CONFIG" >&2
  exit 1
fi
if [[ -e "$CONFIG" && "$(stat -c %u:%g:%h -- "$CONFIG")" != 0:0:1 ]]; then
  echo "Error: update-agent configuration has unsafe ownership or links: $CONFIG" >&2
  exit 1
fi
if [[ ! -e "$CONFIG" ]]; then
  cat >"$CONFIG" <<'EOF'
# HostPanel signed GitHub release updates.
HP_UPDATE_REPOSITORY=1-vps/hostpanel
HP_UPDATE_CHANNEL=stable
HP_UPDATE_TOKEN_FILE=/etc/hostpanel/github-update.token
HP_UPDATE_REQUIRE_TOKEN=yes
HP_UPDATE_PUBLIC_KEY=/etc/hostpanel/update.pub
HP_UPDATE_KEYRING=/etc/hostpanel/update-keyring.json
HP_AUTO_UPDATE=yes
EOF
  chown root:root "$CONFIG"
  chmod 600 "$CONFIG"
else
  [[ -f "$CONFIG" && ! -L "$CONFIG" ]] || {
    echo "Error: unsafe update-agent configuration: $CONFIG" >&2
    exit 1
  }
  chown root:root "$CONFIG"
  chmod 600 "$CONFIG"
  if ! grep -q '^HP_UPDATE_KEYRING=' "$CONFIG"; then
    printf '%s\n' 'HP_UPDATE_KEYRING=/etc/hostpanel/update-keyring.json' >>"$CONFIG"
  fi
fi

TOKEN_FILE=/etc/hostpanel/github-update.token
if [[ -L "$TOKEN_FILE" ]]; then
  echo "Error: unsafe GitHub update token symlink: $TOKEN_FILE" >&2
  exit 1
fi
if [[ -e "$TOKEN_FILE" && "$(stat -c %u:%g:%h -- "$TOKEN_FILE")" != 0:0:1 ]]; then
  echo "Error: GitHub update token has unsafe ownership or links: $TOKEN_FILE" >&2
  exit 1
fi
if [[ -e "$TOKEN_FILE" ]]; then
  [[ -f "$TOKEN_FILE" && ! -L "$TOKEN_FILE" ]] || {
    echo "Error: unsafe GitHub update token file: $TOKEN_FILE" >&2
    exit 1
  }
  chown root:root "$TOKEN_FILE"
  chmod 600 "$TOKEN_FILE"
fi

systemctl daemon-reload
systemctl enable --now hostpanel-update.timer
echo 'HostPanel signed update timer enabled.'
