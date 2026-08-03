"""Safe component maintenance operations for hostpanel-build."""
from __future__ import annotations

import datetime as dt
import fcntl
import os
import pathlib
import shutil
import stat
import tarfile

from hostpanel_build_config import (
    BuildError, Platform, component_packages, expand_component, owner_ids,
    php_versions,
)
from hostpanel_build_packages import (
    apply_system_updates, candidate_version, refresh_packages,
    reinstall_packages, run_command,
)

CONFIG_PATHS = {
    'nginx': ('/etc/nginx',),
    'apache': ('/etc/apache2', '/etc/httpd'),
    'openlitespeed': ('/usr/local/lsws/conf', '/etc/hostpanel/openlitespeed'),
    'php': ('/etc/php', '/etc/opt/remi'),
    'database': (
        '/etc/mysql', '/etc/my.cnf',
        '/var/lib/pgsql/data/postgresql.conf', '/var/lib/pgsql/data/pg_hba.conf',
    ),
    'mail': ('/etc/postfix', '/etc/exim4', '/etc/exim', '/etc/dovecot', '/etc/rspamd'),
    'dns': ('/etc/bind', '/etc/named.conf', '/etc/named'),
    'redis': ('/etc/redis', '/etc/redis.conf'),
}


def require_root() -> None:
    if os.geteuid() != 0:
        raise BuildError('this operation must run as root')


def acquire_lock(path: pathlib.Path):
    uid, gid = owner_ids()
    path.parent.mkdir(parents=True, exist_ok=True)
    parent = path.parent.lstat()
    if (
        not stat.S_ISDIR(parent.st_mode)
        or stat.S_ISLNK(parent.st_mode)
        or parent.st_uid != uid
        or parent.st_gid != gid
        or stat.S_IMODE(parent.st_mode) & 0o022
    ):
        raise BuildError(f'unsafe lock directory: {path.parent}')
    flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as exc:
        raise BuildError(f'could not open build lock safely: {exc}') from exc
    metadata = os.fstat(fd)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != uid
        or metadata.st_gid != gid
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        os.close(fd)
        raise BuildError(f'unsafe build lock: {path}')
    lock = os.fdopen(fd, 'a+b')
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        lock.close()
        raise BuildError('another hostpanel-build operation is running') from exc
    return lock


def config_snapshot(component: str, backup_dir: pathlib.Path) -> pathlib.Path | None:
    paths = [pathlib.Path(item) for item in CONFIG_PATHS.get(component, ()) if os.path.lexists(item)]
    if not paths:
        return None
    backup_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
    metadata = backup_dir.lstat()
    uid, gid = owner_ids()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != uid
        or metadata.st_gid != gid
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise BuildError(f'unsafe build backup directory: {backup_dir}')
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    target = backup_dir / f'{component}-{stamp}.tar.gz'
    with tarfile.open(target, 'w:gz') as archive:
        for path in paths:
            archive.add(path, arcname=str(path).lstrip('/'), recursive=True)
    os.chmod(target, 0o600)
    if os.geteuid() == 0:
        os.chown(target, 0, 0)
    return target


def validation_commands(
    component: str, options: dict[str, str], platform: Platform
) -> list[list[str]]:
    if component == 'nginx':
        return [['nginx', '-t']]
    if component == 'apache':
        return [[('apache2ctl' if platform.family == 'debian' else 'apachectl'), 'configtest']]
    if component == 'openlitespeed':
        binary = '/usr/local/lsws/bin/openlitespeed'
        return [[binary, '-t']] if pathlib.Path(binary).exists() else []
    if component == 'php':
        commands: list[list[str]] = []
        for version in php_versions(options):
            binary = (
                pathlib.Path(f'/usr/sbin/php-fpm{version}')
                if platform.family == 'debian'
                else pathlib.Path(f"/opt/remi/php{version.replace('.', '')}/root/usr/sbin/php-fpm")
            )
            if binary.exists():
                commands.append([str(binary), '-t'])
        return commands
    if component == 'mail':
        commands = [['doveconf', '-n']]
        commands.append(['postfix', 'check'] if options['mta'] == 'postfix' else ['exim', '-bV'])
        return commands
    if component == 'dns':
        return [['named-checkconf']]
    return []


def services_for(component: str, options: dict[str, str], platform: Platform) -> list[str]:
    if component == 'nginx':
        return ['nginx']
    if component == 'apache':
        return ['apache2' if platform.family == 'debian' else 'httpd']
    if component == 'openlitespeed':
        return ['lsws']
    if component == 'php':
        if platform.family == 'debian':
            return [f'php{version}-fpm' for version in php_versions(options)]
        return [f"php{version.replace('.', '')}-php-fpm" for version in php_versions(options)]
    if component == 'database':
        services: list[str] = []
        if options['database'] in {'mariadb', 'both'}:
            services.append('mariadb')
        if options['database'] in {'postgresql', 'both'}:
            services.append('postgresql')
        return services
    if component == 'mail':
        mta = 'postfix' if options['mta'] == 'postfix' else ('exim4' if platform.family == 'debian' else 'exim')
        return [mta, 'dovecot', 'rspamd', 'clamav-daemon' if platform.family == 'debian' else 'clamd']
    if component == 'dns':
        return ['bind9' if platform.family == 'debian' else 'named']
    if component == 'redis':
        return ['redis-server' if platform.family == 'debian' else 'redis']
    return []


def validate_component(
    component: str, options: dict[str, str], platform: Platform,
    log_path: pathlib.Path,
) -> None:
    commands = validation_commands(component, options, platform)
    if component == 'openlitespeed' and not commands:
        raise BuildError('OpenLiteSpeed is not installed after package installation')
    for command in commands:
        if shutil.which(command[0]) is None and not pathlib.Path(command[0]).exists():
            raise BuildError(f'validation command is missing: {command[0]}')
        run_command(command, log_path=log_path)


def restart_component(
    component: str, options: dict[str, str], platform: Platform,
    log_path: pathlib.Path,
) -> None:
    for service in services_for(component, options, platform):
        run_command(['systemctl', 'restart', service], log_path=log_path)
        run_command(['systemctl', 'is-active', '--quiet', service], log_path=log_path)


def run_doctor(
    log_path: pathlib.Path, python_path: pathlib.Path, doctor_path: pathlib.Path
) -> None:
    if not python_path.is_file() or not os.access(python_path, os.X_OK):
        raise BuildError(f'HostPanel Python environment is missing: {python_path}')
    if not doctor_path.is_file():
        raise BuildError(f'hostpanel-doctor is missing: {doctor_path}')
    run_command([str(python_path), str(doctor_path), '--quiet'], log_path=log_path)


def persist_web_mode(path: pathlib.Path, value: str) -> None:
    require_root()
    if value not in {'nginx_apache', 'nginx', 'apache', 'openlitespeed'}:
        raise BuildError('invalid webserver mode')
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'.{path.name}.{os.getpid()}')
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    fd = os.open(temporary, flags, 0o644)
    try:
        os.write(fd, (value + '\n').encode('ascii'))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.chown(temporary, 0, 0)
    os.chmod(temporary, 0o644)
    os.replace(temporary, path)


def reconcile_webserver(
    options: dict[str, str], helper: pathlib.Path, python_path: pathlib.Path,
    mode_file: pathlib.Path, log_path: pathlib.Path,
) -> None:
    require_root()
    if not helper.is_file():
        raise BuildError(f'HostPanel webserver reconciliation helper is missing: {helper}')
    if not python_path.is_file() or not os.access(python_path, os.X_OK):
        raise BuildError(f'HostPanel Python environment is missing: {python_path}')
    run_command([str(python_path), str(helper), options['webserver']], log_path=log_path)
    persist_web_mode(mode_file, options['webserver'])


def validate_webserver_mode(
    options: dict[str, str], helper: pathlib.Path, python_path: pathlib.Path,
    log_path: pathlib.Path,
) -> None:
    if not helper.is_file() or not python_path.is_file():
        raise BuildError('webserver reconciliation runtime is missing')
    completed = run_command(
        [str(python_path), str(helper), options['webserver'], '--check'],
        check=False, capture=True,
    )
    if completed.returncode == 10:
        raise BuildError(
            f"managed domains do not all use {options['webserver']}: "
            + ', '.join(completed.stdout.splitlines()[:10])
        )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()[-500:]
        raise BuildError(f'webserver mode validation failed: {detail}')


def preflight_packages(
    components: list[str], options: dict[str, str], platform: Platform
) -> None:
    missing: list[str] = []
    seen: set[str] = set()
    for component in components:
        for package in component_packages(component, options, platform):
            if package in seen:
                continue
            seen.add(package)
            if candidate_version(package, platform) is None:
                missing.append(package)
    if missing:
        detail = ', '.join(missing)
        if 'openlitespeed' in missing or any(item.startswith('lsphp') for item in missing):
            raise BuildError(
                'OpenLiteSpeed or a required LSPHP runtime is not published by the '
                f'configured repositories for {platform.os_id} {platform.version_id}; '
                f'no services were changed. Missing: {detail}. Configure a supported '
                'official LiteSpeed package source, then rerun: '
                'hostpanel-build build web --apply'
            )
        raise BuildError(
            f'required packages are unavailable; no services were changed: {detail}'
        )


def service_active(name: str) -> bool:
    return run_command(
        ['systemctl', 'is-active', '--quiet', name], check=False, capture=True
    ).returncode == 0


def mask_openlitespeed(log_path: pathlib.Path) -> bool:
    was_active = service_active('lsws.service')
    run_command(
        ['systemctl', 'mask', '--runtime', '--now', 'lsws.service'],
        log_path=log_path,
    )
    return was_active


def unmask_openlitespeed(log_path: pathlib.Path) -> None:
    run_command(
        ['systemctl', 'unmask', '--runtime', 'lsws.service'],
        check=False, log_path=log_path,
    )


def reconcile_webserver_services(
    options: dict[str, str], platform: Platform, log_path: pathlib.Path
) -> None:
    """Enable only the services required by the selected global web mode."""
    mode = options['webserver']
    apache_service = 'apache2' if platform.family == 'debian' else 'httpd'

    run_command(['systemctl', 'enable', '--now', 'nginx.service'], log_path=log_path)
    run_command(['systemctl', 'is-active', '--quiet', 'nginx.service'], log_path=log_path)

    if mode in {'nginx_apache', 'apache'}:
        run_command(
            ['systemctl', 'enable', '--now', f'{apache_service}.service'],
            log_path=log_path,
        )
        run_command(
            ['systemctl', 'is-active', '--quiet', f'{apache_service}.service'],
            log_path=log_path,
        )
    else:
        run_command(
            ['systemctl', 'disable', '--now', f'{apache_service}.service'],
            check=False, log_path=log_path,
        )

    if mode == 'openlitespeed':
        run_command(
            ['systemctl', 'is-active', '--quiet', 'lsws.service'],
            log_path=log_path,
        )
    else:
        run_command(
            ['systemctl', 'disable', '--now', 'lsws.service'],
            check=False, log_path=log_path,
        )


def apply_build(
    component: str, options: dict[str, str], platform: Platform,
    log_path: pathlib.Path, backup_dir: pathlib.Path,
    python_path: pathlib.Path, doctor_path: pathlib.Path, roles: set[str],
    web_helper: pathlib.Path, mode_file: pathlib.Path,
) -> None:
    require_root()
    components = expand_component(component, options, roles if component == 'all' else None)
    if 'panel' in components:
        raise BuildError('use update_panel --apply for the signed HostPanel application update')

    refresh_packages(platform, log_path)
    preflight_packages(components, options, platform)
    ols_requested = 'openlitespeed' in components
    ols_was_active = False
    if ols_requested:
        ols_was_active = mask_openlitespeed(log_path)

    succeeded = False
    try:
        for item in components:
            snapshot = config_snapshot(item, backup_dir)
            if snapshot is not None:
                print(f'Configuration snapshot: {snapshot}')
            reinstall_packages(component_packages(item, options, platform), platform, log_path)
            validate_component(item, options, platform, log_path)
            # OpenLiteSpeed remains runtime-masked until the loopback listener,
            # LSPHP runtimes and all managed vhosts have been reconciled.
            if item != 'openlitespeed':
                restart_component(item, options, platform, log_path)
        if component == 'web' or (component == 'all' and 'web' in roles):
            reconcile_webserver(options, web_helper, python_path, mode_file, log_path)
            reconcile_webserver_services(options, platform, log_path)
            validate_webserver_mode(options, web_helper, python_path, log_path)
        run_doctor(log_path, python_path, doctor_path)
        succeeded = True
    finally:
        if ols_requested:
            unmask_openlitespeed(log_path)
            if not succeeded and ols_was_active:
                run_command(
                    ['systemctl', 'restart', 'lsws.service'],
                    check=False, log_path=log_path,
                )


def apply_updates_and_doctor(
    platform: Platform, log_path: pathlib.Path,
    python_path: pathlib.Path, doctor_path: pathlib.Path,
) -> None:
    require_root()
    apply_system_updates(platform, log_path)
    run_doctor(log_path, python_path, doctor_path)
