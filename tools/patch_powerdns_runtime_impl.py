#!/usr/bin/env python3
"""Patch HostPanel DNS runtime and root helper for selected DNS daemon."""
from __future__ import annotations

import contextlib
import os
import pathlib
import stat

APP_ROOT = pathlib.Path('/opt/hostpanel/app')

CORE_OLD = '''def service_name(logical: str) -> str:\n    """Unit name for a logical service: 'apache' is apache2 or httpd."""\n    return PLATFORM.service(logical)\n'''
CORE_NEW = '''DNS_MODE_FILE = Path("/etc/hostpanel/dns-mode")\n\n\ndef applied_dns_mode() -> str:\n    try:\n        metadata = DNS_MODE_FILE.lstat()\n    except FileNotFoundError:\n        return "bind"\n    except OSError as exc:\n        raise RuntimeError(f"cannot inspect HostPanel DNS mode: {exc}") from exc\n    if (\n        metadata.st_mode & 0o170000 != 0o100000\n        or metadata.st_uid != 0\n        or metadata.st_nlink != 1\n        or metadata.st_mode & 0o022\n    ):\n        raise RuntimeError("unsafe HostPanel DNS mode file")\n    try:\n        mode = DNS_MODE_FILE.read_text(encoding="ascii").strip()\n    except (OSError, UnicodeError) as exc:\n        raise RuntimeError(f"cannot read HostPanel DNS mode: {exc}") from exc\n    if mode not in {"bind", "powerdns"}:\n        raise RuntimeError("invalid HostPanel DNS mode")\n    return mode\n\n\ndef service_name(logical: str) -> str:\n    """Unit name for a logical service, including the applied DNS mode."""\n    if logical == "dns":\n        mode = applied_dns_mode()\n        if mode == "powerdns":\n            return "pdns"\n    return PLATFORM.service(logical)\n'''

ROOT_ANCHOR = '''fi\nphp_tag(){ printf '%s' "${1//./}"; }\n'''
ROOT_MODE = '''fi\n\nDNS_MODE_FILE="/etc/hostpanel/dns-mode"\nDNS_MODE="bind"\nif [[ -e "$DNS_MODE_FILE" || -L "$DNS_MODE_FILE" ]]; then\n  [[ -f "$DNS_MODE_FILE" && ! -L "$DNS_MODE_FILE" ]] \\\n    || die "unsafe HostPanel DNS mode file"\n  IFS=: read -r DNS_MODE_UID DNS_MODE_LINKS DNS_MODE_PERMS \\\n    < <(stat -Lc '%u:%h:%a' -- "$DNS_MODE_FILE") \\\n    || die "cannot inspect HostPanel DNS mode file"\n  [[ "$DNS_MODE_UID" == 0 && "$DNS_MODE_LINKS" == 1 ]] \\\n    || die "unsafe HostPanel DNS mode ownership"\n  case "$DNS_MODE_PERMS" in\n    600|640|644) ;;\n    *) die "unsafe HostPanel DNS mode permissions" ;;\n  esac\n  DNS_MODE="$(tr -d '[:space:]' <"$DNS_MODE_FILE" | tr '[:upper:]' '[:lower:]')" \\\n    || die "cannot read HostPanel DNS mode file"\nfi\ncase "$DNS_MODE" in\n  bind)\n    [[ "$RHEL_FAMILY" == yes ]] && DNS_UNIT="named" || DNS_UNIT="bind9"\n    ;;\n  powerdns) DNS_UNIT="pdns" ;;\n  *) die "invalid HostPanel DNS mode" ;;\nesac\n\nphp_tag(){ printf '%s' "${1//./}"; }\n'''

ALLOW_OLD = 'nginx|apache2|httpd|bind9|named|postfix|'
ALLOW_NEW = 'nginx|apache2|httpd|bind9|named|pdns|postfix|'

DNSSEC_OLD = '''    [[ "$STATE" == on || "$STATE" == off ]] || die "invalid state"\n    [[ -f "$NAMED_CONF" ]] || die "$NAMED_CONF is missing"\n'''
DNSSEC_NEW = '''    [[ "$STATE" == on || "$STATE" == off ]] || die "invalid state"\n    [[ "$DNS_MODE" == bind ]] || die "DNSSEC policy management requires dns=bind"\n    [[ -f "$NAMED_CONF" ]] || die "$NAMED_CONF is missing"\n'''


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
    temporary = path.with_name(f'.{path.name}.powerdns.{os.getpid()}')
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


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if text.count(new) == 1:
        return text
    if text.count(old) != 1:
        raise SystemExit(f'unexpected {label} shape')
    return text.replace(old, new, 1)


def patch_core(path: pathlib.Path) -> None:
    metadata = trusted_file(path)
    text = path.read_text(encoding='utf-8')
    write_atomic(path, replace_once(text, CORE_OLD, CORE_NEW, 'core DNS service'), metadata)


def patch_root(path: pathlib.Path) -> None:
    metadata = trusted_file(path)
    text = path.read_text(encoding='utf-8')
    text = replace_once(text, ROOT_ANCHOR, ROOT_MODE, 'root DNS mode')
    if ALLOW_NEW not in text:
        if text.count(ALLOW_OLD) != 1:
            raise SystemExit('unexpected service allowlist shape')
        text = text.replace(ALLOW_OLD, ALLOW_NEW, 1)
    text = replace_once(text, DNSSEC_OLD, DNSSEC_NEW, 'PowerDNS DNSSEC guard')
    write_atomic(path, text, metadata)


def main() -> int:
    if os.geteuid() != 0:
        raise SystemExit('patch_powerdns_runtime.py must run as root')
    patch_core(APP_ROOT / 'core.py')
    patch_root(APP_ROOT / 'hostpanel-root')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
