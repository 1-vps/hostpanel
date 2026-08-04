#!/usr/bin/env python3
"""Patch installed HostPanel vhost rendering for optional loopback Varnish."""
from __future__ import annotations

import contextlib
import os
import pathlib
import stat

TARGET = pathlib.Path('/opt/hostpanel/app/modules/webserver.py')

CONSTANTS_OLD = 'MODES = ("nginx", "apache", "hybrid", "openlitespeed")\n'
CONSTANTS_NEW = '''MODES = ("nginx", "apache", "hybrid", "openlitespeed")
VARNISH_MODE_FILE = Path("/etc/hostpanel/varnish-mode")
VARNISH_PORT = 6081


def varnish_enabled() -> bool:
    try:
        mode = VARNISH_MODE_FILE.read_text(encoding="ascii").strip()
    except OSError:
        mode = "off"
    if mode == "on":
        return True
    if mode == "off":
        return False
    raise RuntimeError("invalid HostPanel Varnish mode")


def backend_proxy_port(origin_port: int) -> int:
    return VARNISH_PORT if varnish_enabled() else origin_port
'''

HYBRID_OLD = '''            write_root_file(nginx_vhost, NGINX_PROXY_VHOST.format(
                domain=domain, docroot=docroot, port=APACHE_PORT))
'''
HYBRID_NEW = '''            write_root_file(nginx_vhost, NGINX_PROXY_VHOST.format(
                domain=domain, docroot=docroot,
                port=backend_proxy_port(APACHE_PORT)))
'''

OLS_OLD = '''            write_root_file(nginx_vhost, OLS_PROXY_VHOST.format(
                domain=domain, port=OLS_PORT))
'''
OLS_NEW = '''            write_root_file(nginx_vhost, OLS_PROXY_VHOST.format(
                domain=domain, port=backend_proxy_port(OLS_PORT)))
'''

APACHE_OLD = '''def apache_edge_vhost(domain: str, port: int, existing: str) -> str:
    directives = apache_edge_tls_directives(domain, existing)
'''
APACHE_NEW = '''def apache_edge_vhost(domain: str, port: int, existing: str) -> str:
    port = backend_proxy_port(port)
    directives = apache_edge_tls_directives(domain, existing)
'''


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


def replace_once(text: str, old: str, new: str, label: str) -> str:
    old_count = text.count(old)
    new_count = text.count(new)
    if new_count == 1:
        return text
    if old_count == 1:
        return text.replace(old, new, 1)
    raise SystemExit(f'unexpected {label} shape: old={old_count} new={new_count}')


def write_atomic(path: pathlib.Path, text: str, metadata: os.stat_result) -> None:
    temporary = path.with_name(f'.{path.name}.varnish.{os.getpid()}')
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
    text = replace_once(text, CONSTANTS_OLD, CONSTANTS_NEW, 'Varnish runtime constants')
    text = replace_once(text, HYBRID_OLD, HYBRID_NEW, 'hybrid Varnish routing')
    text = replace_once(text, OLS_OLD, OLS_NEW, 'OpenLiteSpeed Varnish routing')
    text = replace_once(text, APACHE_OLD, APACHE_NEW, 'Apache-edge Varnish routing')
    write_atomic(path, text, metadata)


def main() -> int:
    if os.geteuid() != 0:
        raise SystemExit('patch_varnish_runtime.py must run as root')
    patch()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
