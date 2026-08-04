#!/usr/bin/env python3
"""Patch hostpanel-doctor for optional MongoDB and Varnish services."""
from __future__ import annotations

import contextlib
import os
import pathlib
import stat

TARGET = pathlib.Path('/opt/hostpanel/app/hostpanel-doctor')

DNS_BLOCK = '''    if "dns" in roles:\n        dns_mode_path = Path("/etc/hostpanel/dns-mode")\n        try:\n            dns_mode = dns_mode_path.read_text(encoding="ascii").strip()\n        except OSError:\n            dns_mode = "bind"\n        if dns_mode not in {"bind", "powerdns"}:\n            raise RuntimeError("invalid HostPanel DNS mode")\n        expected["pdns" if dns_mode == "powerdns" else service("dns")] = True\n'''

EXTRAS_BLOCK_V1 = DNS_BLOCK + '''    if "database" in roles:\n        mongodb_mode_path = Path("/etc/hostpanel/mongodb-mode")\n        try:\n            mongodb_mode = mongodb_mode_path.read_text(encoding="ascii").strip()\n        except OSError:\n            mongodb_mode = "off"\n        if mongodb_mode not in {"off", "8.0"}:\n            raise RuntimeError("invalid HostPanel MongoDB mode")\n        if mongodb_mode == "8.0":\n            expected["mongod"] = True\n    if "web" in roles:\n        varnish_mode_path = Path("/etc/hostpanel/varnish-mode")\n        try:\n            varnish_mode = varnish_mode_path.read_text(encoding="ascii").strip()\n        except OSError:\n            varnish_mode = "off"\n        if varnish_mode not in {"off", "on"}:\n            raise RuntimeError("invalid HostPanel Varnish mode")\n        if varnish_mode == "on":\n            expected["varnish"] = True\n'''

EXTRAS_BLOCK = '''    def trusted_runtime_mode(path, default, allowed):\n        try:\n            metadata = path.lstat()\n        except FileNotFoundError:\n            return default\n        except OSError as exc:\n            raise RuntimeError(f"cannot inspect HostPanel mode file {path}: {exc}") from exc\n        if (\n            metadata.st_mode & 0o170000 != 0o100000\n            or metadata.st_uid != 0\n            or metadata.st_nlink != 1\n            or metadata.st_mode & 0o022\n        ):\n            raise RuntimeError(f"unsafe HostPanel mode file: {path}")\n        try:\n            mode = path.read_text(encoding="ascii").strip()\n        except (OSError, UnicodeError) as exc:\n            raise RuntimeError(f"cannot read HostPanel mode file {path}: {exc}") from exc\n        if mode not in allowed:\n            raise RuntimeError(f"invalid HostPanel mode in {path}")\n        return mode\n\n    if "dns" in roles:\n        dns_mode = trusted_runtime_mode(\n            Path("/etc/hostpanel/dns-mode"), "bind", {"bind", "powerdns"}\n        )\n        expected["pdns" if dns_mode == "powerdns" else service("dns")] = True\n    if "database" in roles:\n        mongodb_mode = trusted_runtime_mode(\n            Path("/etc/hostpanel/mongodb-mode"), "off", {"off", "8.0"}\n        )\n        if mongodb_mode == "8.0":\n            expected["mongod"] = True\n    if "web" in roles:\n        varnish_mode = trusted_runtime_mode(\n            Path("/etc/hostpanel/varnish-mode"), "off", {"off", "on"}\n        )\n        if varnish_mode == "on":\n            expected["varnish"] = True\n'''


def trusted_file(path: pathlib.Path) -> os.stat_result:
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise SystemExit(f'unsafe HostPanel runtime file: {path}')
    return metadata


def copy_xattrs(source: pathlib.Path, destination: pathlib.Path) -> None:
    required = ('listxattr', 'getxattr', 'setxattr')
    if not all(hasattr(os, name) for name in required):
        raise SystemExit('extended-attribute support is unavailable')
    try:
        for name in os.listxattr(source, follow_symlinks=False):
            value = os.getxattr(source, name, follow_symlinks=False)
            os.setxattr(
                destination, name, value, follow_symlinks=False
            )
    except OSError as exc:
        raise SystemExit(
            f'could not preserve extended metadata for {source}: {exc}'
        ) from exc


def write_atomic(path: pathlib.Path, text: str, metadata: os.stat_result) -> None:
    temporary = path.with_name(f'.{path.name}.extras.{os.getpid()}')
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    fd = -1
    try:
        fd = os.open(temporary, flags, stat.S_IMODE(metadata.st_mode))
        try:
            payload = text.encode('utf-8')
            view = memoryview(payload)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise SystemExit(f'could not write {temporary}')
                view = view[written:]
            os.fchown(fd, metadata.st_uid, metadata.st_gid)
            os.fchmod(fd, stat.S_IMODE(metadata.st_mode))
            copy_xattrs(path, temporary)
            os.fsync(fd)
        finally:
            os.close(fd)
            fd = -1
        os.replace(temporary, path)
    finally:
        if fd >= 0:
            os.close(fd)
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


def patch(path: pathlib.Path = TARGET) -> None:
    metadata = trusted_file(path)
    text = path.read_text(encoding='utf-8')
    if text.count(EXTRAS_BLOCK) == 1:
        return
    if text.count(EXTRAS_BLOCK_V1) == 1:
        updated = text.replace(EXTRAS_BLOCK_V1, EXTRAS_BLOCK, 1)
    elif text.count(DNS_BLOCK) == 1:
        updated = text.replace(DNS_BLOCK, EXTRAS_BLOCK, 1)
    else:
        raise SystemExit('unexpected hostpanel-doctor service block')
    write_atomic(path, updated, metadata)


def main() -> int:
    if os.geteuid() != 0:
        raise SystemExit('patch_extras_doctor.py must run as root')
    patch()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
