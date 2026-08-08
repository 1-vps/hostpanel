#!/usr/bin/env python3
"""Apply one HostPanel webserver mode to every managed domain."""
from __future__ import annotations

import argparse
import grp
import os
import pathlib
import re
import secrets
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


def _capture_xattrs(path: pathlib.Path) -> dict[str, bytes]:
    required = ('listxattr', 'getxattr', 'setxattr', 'removexattr')
    if not all(hasattr(os, name) for name in required):
        raise BuildError('extended-attribute support is unavailable')
    try:
        return {
            name: os.getxattr(path, name, follow_symlinks=False)
            for name in os.listxattr(path, follow_symlinks=False)
        }
    except OSError as exc:
        raise BuildError(f'could not capture extended metadata for {path}: {exc}') from exc


def _apply_xattrs(path: pathlib.Path, values: dict[str, bytes]) -> None:
    try:
        existing = set(os.listxattr(path, follow_symlinks=False))
        desired = set(values)
        for name in existing - desired:
            os.removexattr(path, name, follow_symlinks=False)
        for name, value in values.items():
            os.setxattr(path, name, value, follow_symlinks=False)
    except OSError as exc:
        raise BuildError(f'could not restore extended metadata for {path}: {exc}') from exc


def _open_temporary(path: pathlib.Path, mode: int) -> tuple[int, pathlib.Path]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    for _ in range(32):
        temporary = path.with_name(
            f'.{path.name}.hostpanel-build.{secrets.token_hex(12)}'
        )
        try:
            return os.open(temporary, flags, mode), temporary
        except FileExistsError:
            continue
    raise BuildError(f'could not allocate temporary file for {path}')


def _fsync_parent(path: pathlib.Path) -> None:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, 'O_DIRECTORY'):
        flags |= os.O_DIRECTORY
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    directory_fd = os.open(path.parent, flags)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _replace_atomic(
    path: pathlib.Path, text: str, mode: int, uid: int, gid: int,
    xattrs: dict[str, bytes] | None,
) -> None:
    fd, temporary = _open_temporary(path, mode)
    active_error: BaseException | None = None
    cleanup_error: BaseException | None = None
    try:
        view = memoryview(text.encode('utf-8'))
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise BuildError(f'could not write {temporary}')
            view = view[written:]
        os.fsync(fd)
        os.fchown(fd, uid, gid)
        os.fchmod(fd, mode)
        if xattrs is not None:
            _apply_xattrs(temporary, xattrs)
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


def write_atomic(path: pathlib.Path, text: str) -> None:
    metadata = trusted_root_file(path)
    _replace_atomic(
        path,
        text,
        stat.S_IMODE(metadata.st_mode),
        metadata.st_uid,
        metadata.st_gid,
        _capture_xattrs(path),
    )


def write_new_root_file(path: pathlib.Path, text: str, mode: int, gid: int = 0) -> None:
    xattrs: dict[str, bytes] | None = None
    if os.path.lexists(path):
        trusted_root_file(path)
        xattrs = _capture_xattrs(path)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
    _replace_atomic(path, text, mode, 0, gid, xattrs)


def _replace_symlink(path: pathlib.Path, target: pathlib.Path) -> None:
    temporary = path.with_name(
        f'.{path.name}.hostpanel-build.{secrets.token_hex(12)}'
    )
    try:
        os.symlink(target, temporary)
        os.replace(temporary, path)
        _fsync_parent(path)
    finally:
        temporary.unlink(missing_ok=True)


def _unlink_durable(path: pathlib.Path) -> None:
    if not os.path.lexists(path):
        return
    path.unlink()
    _fsync_parent(path)


def _snapshot_path(
    path: pathlib.Path, *, allow_symlink: bool = False
) -> tuple[object, ...]:
    if not os.path.lexists(path):
        return ('missing',)
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode):
        if not allow_symlink:
            raise BuildError(f'unsafe symlink where a regular file is required: {path}')
        return ('symlink', os.readlink(path))
    if not stat.S_ISREG(metadata.st_mode):
        raise BuildError(f'unsafe managed runtime path: {path}')
    trusted_root_file(path)
    return (
        'file', path.read_text(encoding='utf-8'),
        stat.S_IMODE(metadata.st_mode), metadata.st_uid, metadata.st_gid,
        _capture_xattrs(path),
    )


def _restore_path(path: pathlib.Path, snapshot: tuple[object, ...]) -> None:
    kind = snapshot[0]
    if kind == 'missing':
        _unlink_durable(path)
        return
    if kind == 'symlink':
        _replace_symlink(path, pathlib.Path(str(snapshot[1])))
        return
    if kind == 'file':
        _replace_atomic(
            path,
            str(snapshot[1]),
            int(snapshot[2]),
            int(snapshot[3]),
            int(snapshot[4]),
            dict(snapshot[5]),
        )
        return
    raise BuildError(f'invalid runtime snapshot for {path}')


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
    ensure_root_directory(fcgi_dir, 0o755, 0)
    _replace_symlink(fcgi_dir / 'lsphp', default_binary)
    write_new_root_file(LSPHP_STATE, '\n'.join(v.replace('.', '') for v in versions) + '\n', 0o644)


def prepare_openlitespeed(options: dict[str, str]) -> None:
    if os.geteuid() != 0:
        raise BuildError('OpenLiteSpeed preparation must run as root')
    binary = OLS_ROOT / 'bin/openlitespeed'
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise BuildError('OpenLiteSpeed binary is missing after package installation')
    trusted_root_file(OLS_MAIN)
    trusted_root_file(OLS_ADMIN)

    lsphp_link = OLS_ROOT / 'fcgi-bin/lsphp'
    lsphp_link_snapshot = _snapshot_path(lsphp_link, allow_symlink=True)
    lsphp_state_snapshot = _snapshot_path(LSPHP_STATE)
    main_text = OLS_MAIN.read_text(encoding='utf-8')
    admin_text = OLS_ADMIN.read_text(encoding='utf-8')
    registry_existed = os.path.lexists(OLS_REGISTRY)
    registry_text = ''
    if registry_existed:
        trusted_root_file(OLS_REGISTRY)
        registry_text = OLS_REGISTRY.read_text(encoding='utf-8')

    updated_main = rewrite_main_config(main_text)
    updated_admin = rewrite_admin_config(admin_text)
    gid = group_gid('lsadm')
    hostpanel_gid = group_gid('hostpanel', required=True)
    try:
        ensure_lsphp_runtimes(options)
        ensure_root_directory(OLS_HOSTPANEL, 0o750, gid)
        ensure_root_directory(OLS_VHOSTS, 0o750, gid)
        ensure_root_directory(OLS_STATE_ROOT, 0o750, hostpanel_gid)
        ensure_root_directory(OLS_MARKERS, 0o750, hostpanel_gid)
        ensure_root_directory(OLS_LOGS, 0o750, 0)

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
    except Exception as original_error:
        rollback_errors: list[str] = []
        for path, original, changed in (
            (OLS_MAIN, main_text, updated_main != main_text),
            (OLS_ADMIN, admin_text, updated_admin != admin_text),
        ):
            if not changed:
                continue
            try:
                write_atomic(path, original)
            except Exception as exc:
                rollback_errors.append(f'{path}: {exc}')
        try:
            if registry_existed:
                write_atomic(OLS_REGISTRY, registry_text)
            else:
                _unlink_durable(OLS_REGISTRY)
        except Exception as exc:
            rollback_errors.append(f'{OLS_REGISTRY}: {exc}')
        for path, snapshot in (
            (lsphp_link, lsphp_link_snapshot),
            (LSPHP_STATE, lsphp_state_snapshot),
        ):
            try:
                _restore_path(path, snapshot)
            except Exception as exc:
                rollback_errors.append(f'{path}: {exc}')
        if rollback_errors:
            raise BuildError(
                'OpenLiteSpeed preparation failed and rollback also failed: '
                + '; '.join(rollback_errors)
            ) from original_error
        raise


def _command_text(value: object) -> str:
    if value is None:
        return ''
    if isinstance(value, bytes):
        return value.decode('utf-8', errors='replace').strip()
    return str(value).strip()


def _service_active(name: str) -> bool:
    completed = run(['systemctl', 'is-active', name], check=False)
    state = _command_text(completed.stdout).lower()
    error = _command_text(completed.stderr)
    if completed.returncode == 0 and state == 'active' and not error:
        return True
    if (
        completed.returncode in {3, 4}
        and state in {'inactive', 'failed', 'unknown', 'not-found'}
        and not error
    ):
        return False
    detail = error or state or str(completed.returncode)
    raise BuildError(f'could not determine active state for {name}: {detail}')


def _service_enabled(name: str) -> bool:
    completed = run(['systemctl', 'is-enabled', name], check=False)
    state = _command_text(completed.stdout).lower()
    error = _command_text(completed.stderr)
    if completed.returncode == 0 and state in {'enabled', 'enabled-runtime'} and not error:
        return True
    if (
        completed.returncode in {0, 1, 3, 4}
        and state in {
            'disabled', 'static', 'indirect', 'generated', 'transient',
            'masked', 'masked-runtime', 'not-found',
        }
        and not error
    ):
        return False
    detail = error or state or str(completed.returncode)
    raise BuildError(f'could not determine enabled state for {name}: {detail}')


def _restore_service_state(name: str, was_active: bool, was_enabled: bool) -> list[str]:
    errors: list[str] = []
    commands = (
        ['systemctl', 'enable' if was_enabled else 'disable', name],
        ['systemctl', 'start' if was_active else 'stop', name],
    )
    for command in commands:
        try:
            run(command)
        except BuildError as exc:
            errors.append(str(exc))
    return errors


def activate_openlitespeed() -> None:
    name = 'lsws.service'
    run(['systemctl', 'unmask', '--runtime', name])
    was_active = _service_active(name)
    was_enabled = _service_enabled(name)
    try:
        run(['systemctl', 'enable', '--now', name])
        if not _service_active(name):
            raise BuildError('OpenLiteSpeed did not become active')
    except Exception as original_error:
        rollback_errors = _restore_service_state(
            name, was_active, was_enabled
        )
        if rollback_errors:
            raise BuildError(
                'OpenLiteSpeed activation failed and service rollback also failed: '
                + '; '.join(rollback_errors)
            ) from original_error
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
    if not _service_active('lsws.service'):
        raise BuildError('OpenLiteSpeed service is inactive')


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
        if args.mode == 'openlitespeed':
            activate_openlitespeed()
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