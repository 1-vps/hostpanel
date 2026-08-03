#!/usr/bin/env python3
"""Patch the installed HostPanel runtime to honour the global webserver mode."""
from __future__ import annotations

import os
import pathlib
import stat
import sys


def replace_once(text: str, old: str, new: str, label: str) -> str:
    old_count = text.count(old)
    new_count = text.count(new)
    if new_count == 1:
        return text
    if old_count == 1 and new_count == 0:
        return text.replace(old, new, 1)
    raise SystemExit(f'unexpected {label} shape: old={old_count} new={new_count}')


def trusted_file(path: pathlib.Path) -> None:
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise SystemExit(f'unsafe HostPanel runtime file: {path}')


def write_atomic(path: pathlib.Path, text: str) -> None:
    temporary = path.with_name(f'.{path.name}.custombuild.{os.getpid()}')
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    fd = os.open(temporary, flags, 0o644)
    try:
        payload = text.encode('utf-8')
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise SystemExit(f'could not write {temporary}')
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)
    os.chown(temporary, 0, 0)
    os.chmod(temporary, 0o644)
    os.replace(temporary, path)


def patch_webserver(path: pathlib.Path) -> None:
    trusted_file(path)
    text = path.read_text(encoding='utf-8')
    old = '''MODES = ("nginx", "apache", "hybrid", "openlitespeed")\n'''
    new = '''MODES = ("nginx", "apache", "hybrid", "openlitespeed")\nDEFAULT_MODE_FILE = Path("/etc/hostpanel/webserver-mode")\n\n\ndef default_mode() -> str:\n    """Return the root-managed default for newly created domains."""\n    try:\n        value = DEFAULT_MODE_FILE.read_text(encoding="ascii").strip()\n    except OSError:\n        value = "nginx_apache"\n    mapping = {\n        "nginx_apache": "hybrid",\n        "nginx": "nginx",\n        "apache": "apache",\n        "openlitespeed": "openlitespeed",\n    }\n    require(value in mapping, "Invalid global webserver mode")\n    return mapping[value]\n'''
    write_atomic(path, replace_once(text, old, new, 'webserver default mode'))


def patch_main(path: pathlib.Path) -> None:
    trusted_file(path)
    text = path.read_text(encoding='utf-8')
    old = '''    reload_service("nginx")\n    store.claim(user["user_id"], "domain", domain)\n    return {"ok": True, "domain": domain, "docroot": str(docroot)}\n'''
    new = '''    reload_service("nginx")\n    store.claim(user["user_id"], "domain", domain)\n    try:\n        target_mode = webserver.default_mode()\n        if target_mode != "nginx":\n            webserver.set_mode(domain, target_mode, user)\n    except Exception:\n        store.release("domain", domain)\n        run([binary("sudo"), HOSTPANEL_ROOT, "nginx-site", "disable", domain])\n        reload_service("nginx")\n        raise\n    return {"ok": True, "domain": domain, "docroot": str(docroot),\n            "webserver": webserver.mode_of(domain)}\n'''
    write_atomic(path, replace_once(text, old, new, 'new-domain webserver mode'))


def main() -> int:
    if os.geteuid() != 0:
        raise SystemExit('patch_custombuild_runtime.py must run as root')
    app = pathlib.Path('/opt/hostpanel/app')
    patch_webserver(app / 'modules/webserver.py')
    patch_main(app / 'main.py')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
