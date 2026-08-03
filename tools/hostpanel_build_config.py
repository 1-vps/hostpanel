"""Configuration, platform and component model for hostpanel-build."""
from __future__ import annotations

import contextlib
import os
import pathlib
import re
import stat
import tempfile
from dataclasses import dataclass

DEFAULT_CONFIG = pathlib.Path('/etc/hostpanel/build.conf')
DEFAULT_LOCK = pathlib.Path('/run/hostpanel-build.lock')
DEFAULT_LOG = pathlib.Path('/var/log/hostpanel-build.log')
DEFAULT_BACKUP_DIR = pathlib.Path('/var/backups/hostpanel/custombuild')
DEFAULT_VERSION_FILE = pathlib.Path('/opt/hostpanel/VERSION')
DEFAULT_UPDATER = pathlib.Path('/opt/hostpanel/tools/hostpanel-update')
DEFAULT_DOCTOR = pathlib.Path('/opt/hostpanel/app/hostpanel-doctor')
DEFAULT_ROLES_FILE = pathlib.Path('/etc/hostpanel/roles.conf')
DEFAULT_PYTHON = pathlib.Path('/opt/hostpanel/venv/bin/python')

DEFAULT_OPTIONS = {
    'webservers': 'nginx,apache,openlitespeed',
    'database': 'both',
    'mta': 'postfix',
    'php_versions': '8.5,8.4,8.3,8.2',
}
OPTION_ORDER = tuple(DEFAULT_OPTIONS)
CHOICES = {
    'database': {'mariadb', 'postgresql', 'both'},
    'mta': {'postfix', 'exim'},
}
WEBSERVER_VALUES = {'nginx', 'apache', 'openlitespeed'}
VALID_ROLES = {'control', 'web', 'database', 'mail', 'dns', 'backup', 'edge'}
VALID_COMPONENTS = {
    'panel', 'nginx', 'apache', 'openlitespeed', 'php',
    'database', 'mail', 'dns', 'redis',
}
PHP_RE = re.compile(r'^(?:7\.4|8\.[0-5])$')
KEY_RE = re.compile(r'^[a-z][a-z0-9_]{0,63}$')
MAX_CONFIG_BYTES = 64 * 1024


class BuildError(RuntimeError):
    """A user-facing maintenance failure."""


@dataclass(frozen=True)
class Platform:
    family: str
    os_id: str
    version_id: str


@dataclass(frozen=True)
class PackageState:
    component: str
    package: str
    installed: str | None
    candidate: str | None


def owner_ids() -> tuple[int, int]:
    return (0, 0) if os.geteuid() == 0 else (os.getuid(), os.getgid())


def validate_value(key: str, value: str) -> str:
    value = value.strip().lower()
    if key not in DEFAULT_OPTIONS:
        raise BuildError(f'unknown option: {key}')
    if key in {'php_versions', 'webservers'}:
        parts = [item.strip() for item in value.split(',') if item.strip()]
        maximum = 8 if key == 'php_versions' else 3
        if not parts or len(parts) > maximum:
            raise BuildError(f'{key} must contain 1-{maximum} comma-separated values')
        if key == 'php_versions' and any(PHP_RE.fullmatch(item) is None for item in parts):
            raise BuildError('php_versions supports PHP 7.4 and 8.0 through 8.5')
        if key == 'webservers' and any(item not in WEBSERVER_VALUES for item in parts):
            raise BuildError('webservers supports nginx, apache and openlitespeed')
        if len(set(parts)) != len(parts):
            raise BuildError(f'{key} contains duplicates')
        return ','.join(parts)
    if value not in CHOICES[key]:
        raise BuildError(f"{key} must be one of: {', '.join(sorted(CHOICES[key]))}")
    return value


def parse_config_text(text: str) -> dict[str, str]:
    if len(text.encode('utf-8')) > MAX_CONFIG_BYTES:
        raise BuildError('build configuration is too large')
    values = dict(DEFAULT_OPTIONS)
    seen: set[str] = set()
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if '=' not in line:
            raise BuildError(f'invalid build configuration line {number}')
        key, value = line.split('=', 1)
        key = key.strip()
        if KEY_RE.fullmatch(key) is None or key not in DEFAULT_OPTIONS:
            raise BuildError(f'unknown build option on line {number}: {key}')
        if key in seen:
            raise BuildError(f'duplicate build option on line {number}: {key}')
        seen.add(key)
        values[key] = validate_value(key, value)
    return values


def read_config(path: pathlib.Path) -> dict[str, str]:
    if not os.path.lexists(path):
        return dict(DEFAULT_OPTIONS)
    metadata = path.lstat()
    uid, gid = owner_ids()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != uid
        or metadata.st_gid != gid
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) not in {0o600, 0o640}
    ):
        raise BuildError(f'unsafe build configuration: {path}')
    try:
        text = path.read_bytes().decode('utf-8', errors='strict')
    except UnicodeError as exc:
        raise BuildError('build configuration is not UTF-8') from exc
    return parse_config_text(text)


def render_config(options: dict[str, str]) -> str:
    lines = [
        '# HostPanel CustomBuild-style service options.',
        '# Managed with: hostpanel-build set KEY VALUE',
    ]
    lines.extend(f'{key}={options[key]}' for key in OPTION_ORDER)
    return '\n'.join(lines) + '\n'


def write_config(path: pathlib.Path, options: dict[str, str]) -> None:
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
        raise BuildError(f'unsafe configuration directory: {path.parent}')
    payload = render_config(options).encode('utf-8')
    fd, temporary_name = tempfile.mkstemp(prefix='.build.conf.', dir=path.parent)
    temporary = pathlib.Path(temporary_name)
    try:
        os.fchmod(fd, 0o600)
        os.fchown(fd, uid, gid)
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise BuildError('could not write build configuration')
            view = view[written:]
        os.fsync(fd)
        os.close(fd)
        fd = -1
        os.replace(temporary, path)
        os.chmod(path, 0o600)
        os.chown(path, uid, gid)
    finally:
        if fd >= 0:
            os.close(fd)
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


def detect_platform(path: pathlib.Path = pathlib.Path('/etc/os-release')) -> Platform:
    try:
        text = path.read_text(encoding='utf-8')
    except OSError as exc:
        raise BuildError(f'cannot read {path}: {exc}') from exc
    values: dict[str, str] = {}
    for raw in text.splitlines():
        if '=' in raw:
            key, value = raw.split('=', 1)
            values[key] = value.strip().strip('"')
    os_id = values.get('ID', '').lower()
    like = values.get('ID_LIKE', '').lower().split()
    version_id = values.get('VERSION_ID', '')
    if os_id in {'rocky', 'almalinux', 'rhel', 'centos', 'fedora'} or any(
        item in {'rhel', 'fedora', 'centos'} for item in like
    ):
        return Platform('rhel', os_id, version_id)
    if os_id in {'ubuntu', 'debian'} or 'debian' in like:
        return Platform('debian', os_id, version_id)
    raise BuildError(f"unsupported operating system: {os_id or 'unknown'} {version_id}".strip())


def read_roles(path: pathlib.Path) -> set[str]:
    if not os.path.lexists(path):
        raise BuildError(f'HostPanel role configuration is missing: {path}')
    metadata = path.lstat()
    uid, gid = owner_ids()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_uid != uid
        or metadata.st_gid != gid
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise BuildError(f'unsafe HostPanel role configuration: {path}')
    lines = [
        line.strip() for line in path.read_text(encoding='utf-8').splitlines()
        if line.strip() and not line.lstrip().startswith('#')
    ]
    if len(lines) != 1 or not lines[0].startswith('roles='):
        raise BuildError('roles.conf must contain exactly one roles= line')
    roles = {item for item in lines[0].split('=', 1)[1].replace(',', ' ').split() if item}
    if not roles or not roles <= VALID_ROLES:
        raise BuildError('roles.conf contains unsupported roles')
    return roles


def php_versions(options: dict[str, str]) -> tuple[str, ...]:
    return tuple(options['php_versions'].split(','))


def configured_webservers(options: dict[str, str]) -> list[str]:
    return options['webservers'].split(',')


def component_packages(component: str, options: dict[str, str], platform: Platform) -> list[str]:
    if component == 'panel':
        return []
    if component == 'nginx':
        return ['nginx']
    if component == 'apache':
        return ['apache2'] if platform.family == 'debian' else ['httpd']
    if component == 'openlitespeed':
        return ['openlitespeed']
    if component == 'php':
        if platform.family == 'debian':
            return [f'php{version}-fpm' for version in php_versions(options)]
        return [f"php{version.replace('.', '')}-php-fpm" for version in php_versions(options)]
    if component == 'database':
        packages: list[str] = []
        if options['database'] in {'mariadb', 'both'}:
            packages.append('mariadb-server')
        if options['database'] in {'postgresql', 'both'}:
            packages.append('postgresql' if platform.family == 'debian' else 'postgresql-server')
        return packages
    if component == 'mail':
        mta = 'postfix' if options['mta'] == 'postfix' else ('exim4' if platform.family == 'debian' else 'exim')
        if platform.family == 'debian':
            return [mta, 'dovecot-core', 'rspamd', 'clamav-daemon']
        return [mta, 'dovecot', 'rspamd', 'clamd']
    if component == 'dns':
        return ['bind9'] if platform.family == 'debian' else ['bind']
    if component == 'redis':
        return ['redis-server'] if platform.family == 'debian' else ['redis']
    raise BuildError(f'unsupported component: {component}')


def selected_components(options: dict[str, str], roles: set[str]) -> list[str]:
    components: list[str] = []
    if 'web' in roles:
        components.extend(configured_webservers(options))
        components.append('php')
    elif 'edge' in roles:
        components.append('nginx')
    if 'database' in roles:
        components.extend(['database', 'redis'])
    if 'mail' in roles:
        components.extend(['mail', 'redis'])
    if 'dns' in roles:
        components.append('dns')
    return list(dict.fromkeys(components))


def expand_component(
    component: str, options: dict[str, str], roles: set[str] | None = None
) -> list[str]:
    if component == 'all':
        if roles is None:
            raise BuildError('roles are required for the all target')
        return selected_components(options, roles)
    if component == 'web':
        return [*configured_webservers(options), 'php']
    if component in VALID_COMPONENTS:
        return [component]
    raise BuildError(f'unsupported component: {component}')
