#!/usr/bin/env python3
"""Patch hostpanel-doctor for optional MongoDB and Varnish services."""
from __future__ import annotations

import contextlib
import os
import pathlib
import stat

TARGET = pathlib.Path('/opt/hostpanel/app/hostpanel-doctor')

DNS_BLOCK = '''    if "dns" in roles:\n        dns_mode_path = Path("/etc/hostpanel/dns-mode")\n        try:\n            dns_mode = dns_mode_path.read_text(encoding="ascii").strip()\n        except OSError:\n            dns_mode = "bind"\n        if dns_mode not in {"bind", "powerdns"}:\n            raise RuntimeError("invalid HostPanel DNS mode")\n        expected["pdns" if dns_mode == "powerdns" else service("dns")] = True\n'''

EXTRAS_BLOCK = DNS_BLOCK + '''    if "database" in roles:\n        mongodb_mode_path = Path("/etc/hostpanel/mongodb-mode")\n        try:\n            mongodb_mode = mongodb_mode_path.read_text(encoding="ascii").strip()\n        except OSError:\n            mongodb_mode = "off"\n        if mongodb_mode not in {"off", "8.0"}:\n            raise RuntimeError("invalid HostPanel MongoDB mode")\n        if mongodb_mode == "8.0":\n            expected["mongod"] = True\n    if "web" in roles:\n        varnish_mode_path = Path("/etc/hostpanel/varnish-mode")\n        try:\n            varnish_mode = varnish_mode_path.read_text(encoding="ascii").strip()\n        except OSError:\n            varnish_mode = "off"\n        if varnish_mode not in {"off", "on"}:\n            raise RuntimeError("invalid HostPanel Varnish mode")\n        if varnish_mode == "on":\n            expected["varnish"] = True\n'''


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
            os.fsync(fd)
            os.fchown(fd, metadata.st_uid, metadata.st_gid)
            os.fchmod(fd, stat.S_IMODE(metadata.st_mode))
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
    if text.count(DNS_BLOCK) != 1:
        raise SystemExit('unexpected hostpanel-doctor service block')
    write_atomic(path, text.replace(DNS_BLOCK, EXTRAS_BLOCK, 1), metadata)


def main() -> int:
    if os.geteuid() != 0:
        raise SystemExit('patch_extras_doctor.py must run as root')
    patch()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
