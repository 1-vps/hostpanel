"""Durable runtime-state and rollback wrapper for optional CustomBuild extras."""
from __future__ import annotations

import contextlib
import os
import pathlib
import re
import shutil
import stat

import hostpanel_build_extras as base
from hostpanel_build_config import BuildError, Platform
from hostpanel_build_packages import run_command

MONGODB_MODE_FILE = pathlib.Path('/etc/hostpanel/mongodb-mode')
VARNISH_MODE_FILE = base.VARNISH_MODE_FILE
MONGODB_VERSION = base.MONGODB_VERSION
WEB_MODE_FILE = pathlib.Path('/etc/hostpanel/webserver-mode')

extra_components = base.extra_components
varnish_origin_port = base.varnish_origin_port


def _runtime_mode(path: pathlib.Path, default: str) -> str:
    if not path.is_file():
        return default
    try:
        return path.read_text(encoding='ascii').strip()
    except OSError as exc:
        raise BuildError(f'cannot read runtime mode file: {path}') from exc


def _section_bounds(lines: list[str], name: str) -> tuple[int, int] | None:
    matches: list[int] = []
    pattern = re.compile(rf'^{re.escape(name)}\s*:(.*)$')
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith('#') or line.startswith((' ', '\t')):
            continue
        match = pattern.fullmatch(line)
        if match is None:
            continue
        remainder = match.group(1).split('#', 1)[0].strip()
        if remainder:
            raise BuildError(f'mongod.conf uses an unsupported inline {name} section')
        matches.append(index)
    if len(matches) > 1:
        raise BuildError(f'mongod.conf contains multiple top-level {name} sections')
    if not matches:
        return None
    start = matches[0]
    end = len(lines)
    for index in range(start + 1, len(lines)):
        line = lines[index]
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        if not line.startswith((' ', '\t')):
            end = index
            break
    return start, end


def _nested_indexes(lines: list[str], start: int, end: int, key: str) -> list[int]:
    pattern = re.compile(rf'^\s+{re.escape(key)}\s*:')
    return [index for index in range(start + 1, end) if pattern.match(lines[index])]


def _value_after_colon(line: str) -> str:
    return line.split(':', 1)[1].split('#', 1)[0].strip().strip('"\'')


def harden_mongod_config(text: str) -> str:
    lines = text.splitlines()
    net = _section_bounds(lines, 'net')
    if net is None:
        lines.extend(['', 'net:', '  bindIp: 127.0.0.1'])
    else:
        start, end = net
        bind_all = _nested_indexes(lines, start, end, 'bindIpAll')
        if bind_all:
            raise BuildError('mongod.conf contains net.bindIpAll; refusing ambiguous exposure')
        bind_indexes = _nested_indexes(lines, start, end, 'bindIp')
        if len(bind_indexes) > 1:
            raise BuildError('mongod.conf contains multiple net.bindIp settings')
        if bind_indexes:
            value = _value_after_colon(lines[bind_indexes[0]])
            allowed = {'127.0.0.1', 'localhost', '127.0.0.1,::1', 'localhost,::1'}
            if value not in allowed:
                raise BuildError('mongod.conf has a non-loopback bindIp; refusing to overwrite it')
            lines[bind_indexes[0]] = '  bindIp: 127.0.0.1'
        else:
            lines.insert(start + 1, '  bindIp: 127.0.0.1')

    security = _section_bounds(lines, 'security')
    if security is None:
        lines.extend(['', 'security:', '  authorization: enabled'])
    else:
        start, end = security
        auth_indexes = _nested_indexes(lines, start, end, 'authorization')
        if len(auth_indexes) > 1:
            raise BuildError('mongod.conf contains multiple security.authorization settings')
        if auth_indexes:
            value = _value_after_colon(lines[auth_indexes[0]]).lower()
            if value not in {'enabled', 'true'}:
                raise BuildError('mongod.conf explicitly disables authorization; refusing to overwrite it')
            lines[auth_indexes[0]] = '  authorization: enabled'
        else:
            lines.insert(start + 1, '  authorization: enabled')
    return '\n'.join(lines).rstrip() + '\n'


def _trusted_config(path: pathlib.Path) -> os.stat_result:
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise BuildError(f'unsafe configuration file: {path}')
    return metadata


def _service_active(unit: str) -> bool:
    return run_command(
        ['systemctl', 'is-active', '--quiet', unit], check=False, capture=True
    ).returncode == 0


def _mask_service(unit: str, log_path: pathlib.Path) -> bool:
    was_active = _service_active(unit)
    if was_active:
        run_command(['systemctl', 'stop', unit], log_path=log_path)
    run_command(['systemctl', 'mask', '--runtime', unit], log_path=log_path)
    return was_active


def _unmask_service(unit: str, log_path: pathlib.Path) -> None:
    run_command(
        ['systemctl', 'unmask', '--runtime', unit], check=False, log_path=log_path
    )


def _capture(path: pathlib.Path) -> tuple[bytes, int, int, int] | None:
    if not os.path.lexists(path):
        return None
    metadata = _trusted_config(path)
    return (
        path.read_bytes(), stat.S_IMODE(metadata.st_mode),
        metadata.st_uid, metadata.st_gid,
    )


def _restore(path: pathlib.Path, captured: tuple[bytes, int, int, int] | None) -> None:
    if captured is None:
        path.unlink(missing_ok=True)
        return
    payload, mode, uid, gid = captured
    base.write_atomic_bytes(path, payload, mode, uid, gid)


def validate_mongodb(options: dict[str, str], log_path: pathlib.Path) -> None:
    configured = options.get('mongodb', 'off')
    runtime = _runtime_mode(MONGODB_MODE_FILE, 'off')
    if runtime != configured:
        raise BuildError('MongoDB runtime mode does not match build.conf')
    if configured == 'off':
        if _service_active('mongod.service'):
            raise BuildError('mongod.service is active while mongodb=off')
        return
    if configured != MONGODB_VERSION:
        raise BuildError('mongodb must be off or 8.0')
    if shutil.which('mongod') is None or shutil.which('mongosh') is None:
        raise BuildError('MongoDB binaries are missing')
    if not base.MONGODB_CONFIG.is_file() or base.MONGODB_CONFIG.is_symlink():
        raise BuildError('unsafe or missing /etc/mongod.conf')
    _trusted_config(base.MONGODB_CONFIG)
    current = base.MONGODB_CONFIG.read_text(encoding='utf-8')
    if harden_mongod_config(current) != current:
        raise BuildError('mongod.conf is not in HostPanel hardened form')
    run_command(['systemctl', 'is-active', '--quiet', 'mongod.service'], log_path=log_path)
    if not base.loopback_listener(27017):
        raise BuildError('MongoDB port 27017 is not loopback-only')
    # An unauthenticated ping fails after the first administrator exists. Check
    # that mongosh itself starts without depending on database credentials.
    run_command(
        ['mongosh', '--quiet', '--nodb', '--eval', 'quit(0)'], log_path=log_path
    )


def apply_mongodb(
    options: dict[str, str], platform: Platform, log_path: pathlib.Path,
    backup_dir: pathlib.Path,
) -> None:
    configured = options.get('mongodb', 'off')
    if configured == 'off':
        run_command(
            ['systemctl', 'disable', '--now', 'mongod.service'],
            check=False, log_path=log_path,
        )
        base.write_atomic_text(MONGODB_MODE_FILE, 'off\n')
        validate_mongodb(options, log_path)
        return
    if configured != MONGODB_VERSION:
        raise BuildError('mongodb must be off or 8.0')

    base.require_root()
    base.mongodb_supported(platform)
    snapshot = base.snapshot_paths(
        'mongodb',
        (
            base.MONGODB_CONFIG, base.MONGODB_APT_KEY,
            base.MONGODB_APT_LIST, base.MONGODB_RPM_REPO,
        ),
        backup_dir,
    )
    if snapshot:
        print(f'Configuration snapshot: {snapshot}')
    base.configure_mongodb_repository(platform, log_path)
    base.refresh_packages(platform, log_path)
    if base.candidate_version(base.MONGODB_PACKAGE, platform) is None:
        raise BuildError('MongoDB 8.0 package is unavailable after repository refresh')

    previous_mode = _runtime_mode(MONGODB_MODE_FILE, 'off')
    previous_config = _capture(base.MONGODB_CONFIG)
    was_active = _mask_service('mongod.service', log_path)
    try:
        base.reinstall_packages([base.MONGODB_PACKAGE], platform, log_path)
        if not base.MONGODB_CONFIG.is_file() or base.MONGODB_CONFIG.is_symlink():
            raise BuildError('MongoDB package did not install a safe /etc/mongod.conf')
        metadata = _trusted_config(base.MONGODB_CONFIG)
        updated = harden_mongod_config(
            base.MONGODB_CONFIG.read_text(encoding='utf-8')
        )
        base.write_atomic_text(
            base.MONGODB_CONFIG, updated, stat.S_IMODE(metadata.st_mode)
        )
        _unmask_service('mongod.service', log_path)
        run_command(['systemctl', 'enable', '--now', 'mongod.service'], log_path=log_path)
        base.write_atomic_text(MONGODB_MODE_FILE, MONGODB_VERSION + '\n')
        validate_mongodb(options, log_path)
    except Exception:
        run_command(
            ['systemctl', 'disable', '--now', 'mongod.service'],
            check=False, log_path=log_path,
        )
        with contextlib.suppress(Exception):
            _restore(base.MONGODB_CONFIG, previous_config)
        with contextlib.suppress(Exception):
            base.write_atomic_text(MONGODB_MODE_FILE, previous_mode + '\n')
        _unmask_service('mongod.service', log_path)
        if was_active and previous_mode == MONGODB_VERSION:
            run_command(
                ['systemctl', 'enable', '--now', 'mongod.service'],
                check=False, log_path=log_path,
            )
        raise
    finally:
        _unmask_service('mongod.service', log_path)


def validate_varnish(options: dict[str, str], log_path: pathlib.Path) -> None:
    base.validate_varnish(options, log_path)
    if options.get('varnish') == 'on' and not base.loopback_listener(base.VARNISH_ADMIN_PORT):
        raise BuildError('Varnish management port 6082 is not loopback-only')


def apply_varnish(
    options: dict[str, str], platform: Platform, log_path: pathlib.Path,
    backup_dir: pathlib.Path,
) -> None:
    selected = options.get('webserver', 'nginx_apache')
    applied = _runtime_mode(WEB_MODE_FILE, 'nginx_apache')
    configured = options.get('varnish', 'off')
    runtime = _runtime_mode(VARNISH_MODE_FILE, 'off')
    if applied not in {'nginx_apache', 'nginx', 'apache', 'openlitespeed'}:
        raise BuildError('invalid applied webserver mode')
    if configured == 'on' and selected != applied:
        raise BuildError('apply the selected webserver mode before enabling Varnish')
    effective = dict(options)
    if configured == 'off' and runtime == 'on':
        effective['webserver'] = applied
    origin_port = (
        base.varnish_origin_port(effective)
        if configured == 'on'
        else (8088 if effective.get('webserver') == 'openlitespeed' else 8080)
    )

    base.require_root()
    snapshot = base.snapshot_paths(
        'varnish',
        (base.VARNISH_VCL, base.VARNISH_DROPIN, VARNISH_MODE_FILE, base.NGINX_AVAILABLE),
        backup_dir,
    )
    if snapshot:
        print(f'Configuration snapshot: {snapshot}')

    previous_mode = runtime
    previous_active = _service_active('varnish.service')
    previous_vcl = _capture(base.VARNISH_VCL)
    previous_dropin = _capture(base.VARNISH_DROPIN)

    if configured == 'off':
        try:
            base.write_atomic_text(VARNISH_MODE_FILE, 'off\n')
            base.rewrite_varnish_proxies(False, origin_port, log_path)
            run_command(
                ['systemctl', 'disable', '--now', 'varnish.service'],
                check=False, log_path=log_path,
            )
            validate_varnish(effective, log_path)
            return
        except Exception:
            with contextlib.suppress(Exception):
                base.write_atomic_text(VARNISH_MODE_FILE, previous_mode + '\n')
            if previous_mode == 'on':
                with contextlib.suppress(Exception):
                    base.rewrite_varnish_proxies(True, origin_port, log_path)
            if previous_active:
                run_command(
                    ['systemctl', 'enable', '--now', 'varnish.service'],
                    check=False, log_path=log_path,
                )
            raise

    base.refresh_packages(platform, log_path)
    if base.candidate_version(base.VARNISH_PACKAGE, platform) is None:
        raise BuildError('Varnish package is unavailable from configured repositories')

    _mask_service('varnish.service', log_path)
    try:
        base.reinstall_packages([base.VARNISH_PACKAGE], platform, log_path)
        binary = shutil.which('varnishd')
        if binary is None:
            raise BuildError('varnishd is missing after package installation')
        base.VARNISH_VCL.parent.mkdir(parents=True, mode=0o755, exist_ok=True)
        base.write_atomic_text(
            base.VARNISH_VCL, base.render_varnish_vcl(origin_port), 0o644
        )
        secret_candidates = (
            pathlib.Path('/etc/varnish/secret'),
            pathlib.Path('/etc/varnish/varnish_secret'),
        )
        secret = next((path for path in secret_candidates if path.is_file()), None)
        base.write_atomic_text(
            base.VARNISH_DROPIN, base.render_varnish_dropin(binary, secret), 0o644
        )
        run_command([binary, '-C', '-f', str(base.VARNISH_VCL)], log_path=log_path)
        run_command(['systemctl', 'daemon-reload'], log_path=log_path)
        _unmask_service('varnish.service', log_path)
        run_command(['systemctl', 'enable', '--now', 'varnish.service'], log_path=log_path)
        run_command(['systemctl', 'is-active', '--quiet', 'varnish.service'], log_path=log_path)
        if not base.loopback_listener(base.VARNISH_PORT):
            raise BuildError('Varnish did not bind exclusively to 127.0.0.1:6081')
        if not base.loopback_listener(base.VARNISH_ADMIN_PORT):
            raise BuildError('Varnish did not bind management to loopback port 6082')
        base.write_atomic_text(VARNISH_MODE_FILE, 'on\n')
        base.rewrite_varnish_proxies(True, origin_port, log_path)
        validate_varnish(effective, log_path)
    except Exception:
        run_command(
            ['systemctl', 'disable', '--now', 'varnish.service'],
            check=False, log_path=log_path,
        )
        with contextlib.suppress(Exception):
            _restore(base.VARNISH_VCL, previous_vcl)
            _restore(base.VARNISH_DROPIN, previous_dropin)
            run_command(['systemctl', 'daemon-reload'], check=False, log_path=log_path)
        with contextlib.suppress(Exception):
            base.write_atomic_text(VARNISH_MODE_FILE, previous_mode + '\n')
        _unmask_service('varnish.service', log_path)
        if previous_active and previous_mode == 'on':
            run_command(
                ['systemctl', 'enable', '--now', 'varnish.service'],
                check=False, log_path=log_path,
            )
        raise
    finally:
        _unmask_service('varnish.service', log_path)


def ensure_safe_web_switch(
    component: str, options: dict[str, str], mode_file: pathlib.Path
) -> None:
    if component not in {'web', 'all'}:
        return
    try:
        current_web = mode_file.read_text(encoding='ascii').strip()
    except OSError:
        current_web = 'nginx_apache'
    runtime_varnish = _runtime_mode(VARNISH_MODE_FILE, 'off')
    if runtime_varnish not in {'off', 'on'}:
        raise BuildError('invalid Varnish runtime mode')
    if runtime_varnish == 'on' and current_web != options['webserver']:
        raise BuildError(
            'disable active Varnish before changing webserver mode: '
            'hostpanel-build set varnish off && '
            'hostpanel-build build varnish --apply'
        )


def install() -> None:
    base.harden_mongod_config = harden_mongod_config
