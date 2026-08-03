#!/usr/bin/env python3
"""Apply one HostPanel webserver mode to every managed domain."""
from __future__ import annotations

import argparse
import grp
import os
import pathlib
import re
import stat
import subprocess
import sys

TOOL_ROOT = pathlib.Path(__file__).resolve().parent
if str(TOOL_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOL_ROOT))

from hostpanel_build_config import BuildError, DEFAULT_CONFIG, php_versions, read_config

APP_ROOT = pathlib.Path('/opt/hostpanel/app')
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import store  # type: ignore  # noqa: E402
from modules import webserver  # type: ignore  # noqa: E402

MODE_MAP = {
    'nginx_apache': 'hybrid',
    'nginx': 'nginx',
    'apache': 'apache',
    'openlitespeed': 'openlitespeed',
}
OLS_ROOT = pathlib.Path('/usr/local/lsws')
OLS_MAIN = OLS_ROOT / 'conf/httpd_config.conf'
OLS_ADMIN = OLS_ROOT / 'admin/conf/admin_config.conf'
OLS_HOSTPANEL = OLS_ROOT / 'conf/hostpanel'
OLS_VHOSTS = OLS_ROOT / 'conf/vhosts'
OLS_REGISTRY = OLS_HOSTPANEL / 'hostpanel.conf'
OLS_STATE_ROOT = pathlib.Path('/etc/hostpanel/openlitespeed')
OLS_MARKERS = OLS_STATE_ROOT / 'domains'
OLS_LOGS = pathlib.Path('/var/log/hostpanel/openlitespeed')
LSPHP_STATE = pathlib.Path('/etc/hostpanel/lsphp-versions')
OLS_INCLUDE = 'include $SERVER_ROOT/conf/hostpanel/*.conf'


def managed_domains() -> list[str]:
    with store.connect() as database:
        rows = database.execute(
            "SELECT name FROM resources WHERE kind='domain' ORDER BY name"
        ).fetchall()
    return [str(row['name']) for row in rows]


def run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    for name in ('PYTHONPATH', 'PYTHONHOME', 'BASH_ENV', 'ENV', 'LD_PRELOAD', 'LD_LIBRARY_PATH'):
        environment.pop(name, None)
    environment['PATH'] = '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'
    completed = subprocess.run(
        command, env=environment, text=True, capture_output=True, check=False
    )
    if check and completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()[-500:]
        raise BuildError(f"command failed ({completed.returncode}): {' '.join(command)}: {detail}")
    return completed


def rewrite_main_config(text: str) -> str:
    address_pattern = re.compile(r'(?m)^([ \t]*address[ \t]+)(\S+):8088[ \t]*$')
    matches = list(address_pattern.finditer(text))
    if len(matches) > 1:
        raise BuildError('OpenLiteSpeed main configuration has multiple port 8088 listeners')
    if matches:
        host = matches[0].group(2)
        if host not in {'*', '0.0.0.0', '[::]', '127.0.0.1'}:
            raise BuildError('OpenLiteSpeed port 8088 is already assigned to a custom listener')
        text = address_pattern.sub(r'\g<1>127.0.0.1:8099', text, count=1)

    proxy_pattern = re.compile(r'(?m)^[ \t]*useIpInProxyHeader[ \t]+\S+[ \t]*$')
    proxy_matches = proxy_pattern.findall(text)
    if len(proxy_matches) > 1:
        raise BuildError('OpenLiteSpeed has multiple useIpInProxyHeader directives')
    if proxy_matches:
        text = proxy_pattern.sub('useIpInProxyHeader 1', text, count=1)
    else:
        text = text.rstrip() + '\n\nuseIpInProxyHeader 1\n'

    if OLS_INCLUDE not in text:
        text = text.rstrip() + f'\n\n# HostPanel managed virtual hosts\n{OLS_INCLUDE}\n'
    return text


def rewrite_admin_config(text: str) -> str:
    pattern = re.compile(r'(?m)^([ \t]*address[ \t]+)(\S+):7080[ \t]*$')
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise BuildError('OpenLiteSpeed WebAdmin must contain exactly one port 7080 listener')
    host = matches[0].group(2)
    if host not in {'*', '0.0.0.0', '[::]', '127.0.0.1'}:
        raise BuildError('OpenLiteSpeed WebAdmin uses an unsupported custom address')
    return pattern.sub(r'\g<1>127.0.0.1:7080', text, count=1)


def trusted_root_file(path: pathlib.Path) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise BuildError(f'missing OpenLiteSpeed configuration file: {path}') from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise BuildError(f'unsafe OpenLiteSpeed configuration file: {path}')
    return metadata


def write_atomic(path: pathlib.Path, text: str) -> None:
    metadata = trusted_root_file(path)
    temporary = path.with_name(f'.{path.name}.hostpanel-build.{os.getpid()}')
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    fd = os.open(temporary, flags, stat.S_IMODE(metadata.st_mode))
    try:
        os.fchown(fd, metadata.st_uid, metadata.st_gid)
        payload = text.encode('utf-8')
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise BuildError(f'could not write {temporary}')
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(temporary, path)


def write_new_root_file(path: pathlib.Path, text: str, mode: int, gid: int = 0) -> None:
    if os.path.lexists(path):
        trusted_root_file(path)
        write_atomic(path, text)
        os.chmod(path, mode)
        os.chown(path, 0, gid)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'.{path.name}.hostpanel-build.{os.getpid()}')
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    fd = os.open(temporary, flags, mode)
    try:
        os.fchown(fd, 0, gid)
        view = memoryview(text.encode('utf-8'))
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise BuildError(f'could not write {temporary}')
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(temporary, path)


def group_gid(name: str, *, required: bool = False) -> int:
    try:
        return grp.getgrnam(name).gr_gid
    except KeyError as exc:
        if required:
            raise BuildError(f'required service group is missing: {name}') from exc
        return 0


def ensure_root_directory(path: pathlib.Path, mode: int, gid: int = 0) -> None:
    path.mkdir(parents=True, exist_ok=True)
    metadata = path.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise BuildError(f'unsafe OpenLiteSpeed directory: {path}')
    os.chown(path, 0, gid)
    os.chmod(path, mode)


def validate_lsphp_runtimes(options: dict[str, str]) -> tuple[str, ...]:
    versions = php_versions(options)
    missing: list[str] = []
    for version in versions:
        binary = OLS_ROOT / f"lsphp{version.replace('.', '')}/bin/lsphp"
        if not binary.is_file() or not os.access(binary, os.X_OK):
            missing.append(version)
    if missing:
        raise BuildError('missing installed LSPHP runtimes: ' + ', '.join(missing))
    return versions


def ensure_lsphp_runtimes(options: dict[str, str]) -> None:
    versions = validate_lsphp_runtimes(options)
    default_binary = OLS_ROOT / f"lsphp{versions[0].replace('.', '')}/bin/lsphp"
    fcgi_dir = OLS_ROOT / 'fcgi-bin'
    fcgi_dir.mkdir(parents=True, exist_ok=True)
    link = fcgi_dir / 'lsphp'
    temporary = fcgi_dir / f'.lsphp.hostpanel-build.{os.getpid()}'
    temporary.unlink(missing_ok=True)
    os.symlink(default_binary, temporary)
    os.replace(temporary, link)
    write_new_root_file(LSPHP_STATE, '\n'.join(v.replace('.', '') for v in versions) + '\n', 0o644)


def prepare_openlitespeed(options: dict[str, str]) -> None:
    if os.geteuid() != 0:
        raise BuildError('OpenLiteSpeed preparation must run as root')
    binary = OLS_ROOT / 'bin/openlitespeed'
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise BuildError('OpenLiteSpeed binary is missing after package installation')
    trusted_root_file(OLS_MAIN)
    trusted_root_file(OLS_ADMIN)
    ensure_lsphp_runtimes(options)

    gid = group_gid('lsadm')
    hostpanel_gid = group_gid('hostpanel', required=True)
    ensure_root_directory(OLS_HOSTPANEL, 0o750, gid)
    ensure_root_directory(OLS_VHOSTS, 0o750, gid)
    ensure_root_directory(OLS_STATE_ROOT, 0o750, hostpanel_gid)
    ensure_root_directory(OLS_MARKERS, 0o750, hostpanel_gid)
    ensure_root_directory(OLS_LOGS, 0o750, 0)

    main_text = OLS_MAIN.read_text(encoding='utf-8')
    admin_text = OLS_ADMIN.read_text(encoding='utf-8')
    registry_existed = OLS_REGISTRY.exists()
    registry_text = ''
    if registry_existed:
        trusted_root_file(OLS_REGISTRY)
        registry_text = OLS_REGISTRY.read_text(encoding='utf-8')

    updated_main = rewrite_main_config(main_text)
    updated_admin = rewrite_admin_config(admin_text)
    try:
        if updated_main != main_text:
            write_atomic(OLS_MAIN, updated_main)
        if updated_admin != admin_text:
            write_atomic(OLS_ADMIN, updated_admin)

        if not registry_existed:
            write_new_root_file(
                OLS_REGISTRY,
                '# Managed by HostPanel — domains are added by app/hostpanel-root\n'
                'listener HostPanel {\n'
                '  address 127.0.0.1:8088\n'
                '  secure 0\n'
                '}\n',
                0o640,
                gid,
            )

        run([str(binary), '-t'])
        run(['systemctl', 'unmask', '--runtime', 'lsws.service'], check=False)
        run(['systemctl', 'enable', '--now', 'lsws.service'])
        run(['systemctl', 'is-active', '--quiet', 'lsws.service'])
    except Exception:
        if updated_main != main_text:
            write_atomic(OLS_MAIN, main_text)
        if updated_admin != admin_text:
            write_atomic(OLS_ADMIN, admin_text)
        if registry_existed:
            write_atomic(OLS_REGISTRY, registry_text)
        else:
            OLS_REGISTRY.unlink(missing_ok=True)
        raise


def check_openlitespeed(options: dict[str, str]) -> None:
    binary = OLS_ROOT / 'bin/openlitespeed'
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise BuildError('OpenLiteSpeed is not installed')
    validate_lsphp_runtimes(options)
    trusted_root_file(OLS_MAIN)
    trusted_root_file(OLS_ADMIN)
    trusted_root_file(OLS_REGISTRY)
    main = OLS_MAIN.read_text(encoding='utf-8')
    admin = OLS_ADMIN.read_text(encoding='utf-8')
    registry = OLS_REGISTRY.read_text(encoding='utf-8')
    if OLS_INCLUDE not in main or not re.search(r'(?m)^[ \t]*useIpInProxyHeader[ \t]+1[ \t]*$', main):
        raise BuildError('OpenLiteSpeed main configuration is missing HostPanel proxy controls')
    if re.search(r'(?m)^[ \t]*address[ \t]+(?!127\.0\.0\.1:8099[ \t]*$)\S+:8088[ \t]*$', main):
        raise BuildError('OpenLiteSpeed main configuration exposes port 8088')
    if not re.search(r'(?m)^[ \t]*address[ \t]+127\.0\.0\.1:7080[ \t]*$', admin):
        raise BuildError('OpenLiteSpeed WebAdmin is not loopback-only')
    if not re.search(r'(?m)^[ \t]*address[ \t]+127\.0\.0\.1:8088[ \t]*$', registry):
        raise BuildError('HostPanel OpenLiteSpeed listener is not loopback-only')
    run([str(binary), '-t'])
    run(['systemctl', 'is-active', '--quiet', 'lsws.service'])


def rollback_domains(
    changed: list[str], previous: dict[str, str], admin: dict[str, object]
) -> list[str]:
    failures: list[str] = []
    for domain in reversed(changed):
        try:
            webserver.set_mode(domain, previous[domain], admin)
        except Exception as exc:  # pragma: no cover - only reached on host rollback failure
            failures.append(f'{domain}: {exc}')
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=tuple(MODE_MAP))
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args(argv)
    options = read_config(DEFAULT_CONFIG)
    if options['webserver'] != args.mode:
        raise BuildError('build.conf webserver value changed during reconciliation')

    target = MODE_MAP[args.mode]
    domains = managed_domains()
    if args.check:
        if args.mode == 'openlitespeed':
            check_openlitespeed(options)
        mismatches = [name for name in domains if webserver.mode_of(name) != target]
        if mismatches:
            print('\n'.join(mismatches))
            return 10
        print(f'All {len(domains)} managed domains use {args.mode}.')
        return 0

    if args.mode == 'openlitespeed':
        prepare_openlitespeed(options)

    admin: dict[str, object] = {'role': 'admin', 'user_id': 0, 'username': 'root'}
    previous = {domain: webserver.mode_of(domain) for domain in domains}
    changed: list[str] = []
    try:
        for domain in domains:
            result = webserver.set_mode(domain, target, admin)
            if result.get('changed'):
                changed.append(domain)
        mismatches = [name for name in domains if webserver.mode_of(name) != target]
        if mismatches:
            raise BuildError('post-switch validation failed for: ' + ', '.join(mismatches[:10]))
    except Exception as exc:
        failures = rollback_domains(changed, previous, admin)
        if failures:
            raise BuildError(
                f'webserver switch failed ({exc}); rollback also failed: ' + '; '.join(failures)
            ) from exc
        raise BuildError(f'webserver switch failed and was rolled back: {exc}') from exc

    print(f'Applied {args.mode} to {len(domains)} domains ({len(changed)} changed).')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except BuildError as exc:
        print(f'hostpanel-build-web failed: {exc}', file=sys.stderr)
        raise SystemExit(1)
