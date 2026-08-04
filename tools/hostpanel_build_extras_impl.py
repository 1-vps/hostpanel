"""Optional MongoDB and Varnish components for HostPanel CustomBuild."""
from __future__ import annotations

import contextlib
import os
import pathlib
import platform as runtime_platform
import pwd
import re
import shutil
import stat
import tarfile
import tempfile
from typing import Iterable

from hostpanel_build_config import BuildError, Platform
from hostpanel_build_packages import (
    candidate_version, refresh_packages, reinstall_packages, run_command,
)

MONGODB_VERSION = '8.0'
MONGODB_PACKAGE = 'mongodb-org'
MONGODB_KEY_URL = 'https://pgp.mongodb.com/server-8.0.asc'
MONGODB_KEY_FINGERPRINT = '4B0752C1BCA238C0B4EE14DC41DE058A4E7DCA05'
MONGODB_APT_KEY = pathlib.Path('/usr/share/keyrings/mongodb-server-8.0.gpg')
MONGODB_APT_LIST = pathlib.Path('/etc/apt/sources.list.d/mongodb-org-8.0.list')
MONGODB_RPM_REPO = pathlib.Path('/etc/yum.repos.d/mongodb-org-8.0.repo')
MONGODB_CONFIG = pathlib.Path('/etc/mongod.conf')

VARNISH_PACKAGE = 'varnish'
VARNISH_PORT = 6081
VARNISH_ADMIN_PORT = 6082
VARNISH_MODE_FILE = pathlib.Path('/etc/hostpanel/varnish-mode')
VARNISH_VCL = pathlib.Path('/etc/varnish/default.vcl')
VARNISH_DROPIN = pathlib.Path('/etc/systemd/system/varnish.service.d/hostpanel.conf')
NGINX_AVAILABLE = pathlib.Path('/etc/nginx/sites-available')


def require_root() -> None:
    if os.geteuid() != 0:
        raise BuildError('this operation must run as root')


def write_atomic_bytes(
    path: pathlib.Path, payload: bytes, mode: int = 0o644, uid: int = 0, gid: int = 0
) -> None:
    require_root()
    path.parent.mkdir(parents=True, mode=0o755, exist_ok=True)
    parent = path.parent.lstat()
    if (
        not stat.S_ISDIR(parent.st_mode)
        or stat.S_ISLNK(parent.st_mode)
        or parent.st_uid != 0
        or stat.S_IMODE(parent.st_mode) & 0o022
    ):
        raise BuildError(f'unsafe configuration directory: {path.parent}')
    if os.path.lexists(path):
        metadata = path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_nlink != 1
        ):
            raise BuildError(f'unsafe configuration file: {path}')
    temporary = path.with_name(f'.{path.name}.hostpanel-build.{os.getpid()}')
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    fd = os.open(temporary, flags, mode)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise BuildError(f'could not write {temporary}')
            view = view[written:]
        os.fsync(fd)
        os.fchown(fd, uid, gid)
        os.fchmod(fd, mode)
    finally:
        os.close(fd)
    os.replace(temporary, path)


def write_atomic_text(path: pathlib.Path, text: str, mode: int = 0o644) -> None:
    write_atomic_bytes(path, text.encode('utf-8'), mode)


def snapshot_paths(
    name: str, paths: Iterable[pathlib.Path], backup_dir: pathlib.Path
) -> pathlib.Path | None:
    existing = [path for path in paths if os.path.lexists(path)]
    if not existing:
        return None
    backup_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
    metadata = backup_dir.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise BuildError(f'unsafe build backup directory: {backup_dir}')
    target = backup_dir / f'{name}-{os.getpid()}.tar.gz'
    with tarfile.open(target, 'w:gz') as archive:
        for path in existing:
            archive.add(path, arcname=str(path).lstrip('/'), recursive=True)
    os.chown(target, 0, 0)
    os.chmod(target, 0o600)
    return target


def extra_components(options: dict[str, str], roles: set[str]) -> list[str]:
    result: list[str] = []
    if 'database' in roles and options.get('mongodb') == MONGODB_VERSION:
        result.append('mongodb')
    if 'web' in roles and options.get('varnish') == 'on':
        result.append('varnish')
    return result


def mongodb_supported(platform: Platform, machine: str | None = None) -> tuple[str, str]:
    machine = (machine or runtime_platform.machine()).lower()
    if machine not in {'x86_64', 'amd64'}:
        raise BuildError('MongoDB 8.0 CustomBuild support is limited to x86_64 hosts')
    if platform.os_id == 'ubuntu':
        mapping = {'20.04': 'focal', '22.04': 'jammy', '24.04': 'noble'}
        if platform.version_id in mapping:
            return ('ubuntu', mapping[platform.version_id])
    if platform.os_id == 'debian' and platform.version_id == '12':
        return ('debian', 'bookworm')
    if platform.os_id in {'rhel', 'rocky', 'almalinux', 'centos'}:
        major = platform.version_id.split('.', 1)[0]
        if major in {'8', '9'}:
            return ('rhel', major)
    raise BuildError(
        f'MongoDB 8.0 is not supported by this CustomBuild path on '
        f'{platform.os_id} {platform.version_id}'
    )


def verify_mongodb_key(path: pathlib.Path) -> None:
    completed = run_command(
        ['gpg', '--batch', '--show-keys', '--with-colons', str(path)],
        check=False, capture=True,
    )
    if completed.returncode != 0:
        raise BuildError('MongoDB repository signing key could not be parsed')
    fingerprints = {
        line.split(':')[9].upper()
        for line in completed.stdout.splitlines()
        if line.startswith('fpr:') and len(line.split(':')) > 9
    }
    if MONGODB_KEY_FINGERPRINT not in fingerprints:
        raise BuildError('MongoDB repository signing key fingerprint does not match')


def configure_mongodb_repository(platform: Platform, log_path: pathlib.Path) -> None:
    require_root()
    family, release = mongodb_supported(platform)
    for command in ('curl', 'gpg'):
        if shutil.which(command) is None:
            raise BuildError(f'{command} is required to configure the MongoDB repository')
    run_root = pathlib.Path('/run/hostpanel-build')
    run_root.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chown(run_root, 0, 0)
    os.chmod(run_root, 0o700)
    with tempfile.TemporaryDirectory(prefix='mongodb.', dir=run_root) as directory:
        root = pathlib.Path(directory)
        key = root / 'server-8.0.asc'
        keyring = root / 'server-8.0.gpg'
        run_command([
            'curl', '-q', '--fail', '--silent', '--show-error', '--location',
            '--proto', '=https', '--tlsv1.2', '--output', str(key), MONGODB_KEY_URL,
        ], log_path=log_path)
        verify_mongodb_key(key)
        run_command([
            'gpg', '--batch', '--yes', '--dearmor', '--output', str(keyring), str(key)
        ], log_path=log_path)
        if family in {'ubuntu', 'debian'}:
            write_atomic_bytes(MONGODB_APT_KEY, keyring.read_bytes(), 0o644)

    if family == 'ubuntu':
        MONGODB_RPM_REPO.unlink(missing_ok=True)
        line = (
            f'deb [ arch=amd64 signed-by={MONGODB_APT_KEY} ] '
            f'https://repo.mongodb.org/apt/ubuntu {release}/mongodb-org/8.0 multiverse\n'
        )
        write_atomic_text(MONGODB_APT_LIST, line)
    elif family == 'debian':
        MONGODB_RPM_REPO.unlink(missing_ok=True)
        line = (
            f'deb [ arch=amd64 signed-by={MONGODB_APT_KEY} ] '
            f'https://repo.mongodb.org/apt/debian {release}/mongodb-org/8.0 main\n'
        )
        write_atomic_text(MONGODB_APT_LIST, line)
    else:
        MONGODB_APT_KEY.unlink(missing_ok=True)
        MONGODB_APT_LIST.unlink(missing_ok=True)
        repo = (
            '[mongodb-org-8.0]\n'
            'name=MongoDB Repository\n'
            f'baseurl=https://repo.mongodb.org/yum/redhat/{release}/mongodb-org/8.0/$basearch/\n'
            'gpgcheck=1\n'
            'enabled=1\n'
            f'gpgkey={MONGODB_KEY_URL}\n'
        )
        write_atomic_text(MONGODB_RPM_REPO, repo)


def harden_mongod_config(text: str) -> str:
    lines = text.splitlines()
    net_index = next((i for i, line in enumerate(lines) if line.strip() == 'net:' and not line.startswith((' ', '\t'))), None)
    if net_index is None:
        lines.extend(['', 'net:', '  bindIp: 127.0.0.1'])
    else:
        end = next(
            (i for i in range(net_index + 1, len(lines)) if lines[i] and not lines[i].startswith((' ', '\t', '#'))),
            len(lines),
        )
        bind_indexes = [
            i for i in range(net_index + 1, end)
            if re.match(r'^\s+bindIp\s*:', lines[i])
        ]
        if len(bind_indexes) > 1:
            raise BuildError('mongod.conf contains multiple net.bindIp settings')
        if bind_indexes:
            value = lines[bind_indexes[0]].split(':', 1)[1].strip().strip('"\'')
            allowed = {'127.0.0.1', 'localhost', '127.0.0.1,::1', 'localhost,::1'}
            if value not in allowed:
                raise BuildError('mongod.conf has a non-loopback bindIp; refusing to overwrite it')
            lines[bind_indexes[0]] = '  bindIp: 127.0.0.1'
        else:
            lines.insert(net_index + 1, '  bindIp: 127.0.0.1')

    security_index = next((i for i, line in enumerate(lines) if line.strip() == 'security:' and not line.startswith((' ', '\t'))), None)
    if security_index is None:
        lines.extend(['', 'security:', '  authorization: enabled'])
    else:
        end = next(
            (i for i in range(security_index + 1, len(lines)) if lines[i] and not lines[i].startswith((' ', '\t', '#'))),
            len(lines),
        )
        auth_indexes = [
            i for i in range(security_index + 1, end)
            if re.match(r'^\s+authorization\s*:', lines[i])
        ]
        if len(auth_indexes) > 1:
            raise BuildError('mongod.conf contains multiple security.authorization settings')
        if auth_indexes:
            value = lines[auth_indexes[0]].split(':', 1)[1].strip().lower()
            if value not in {'enabled', 'true'}:
                raise BuildError('mongod.conf explicitly disables authorization; refusing to overwrite it')
            lines[auth_indexes[0]] = '  authorization: enabled'
        else:
            lines.insert(security_index + 1, '  authorization: enabled')
    return '\n'.join(lines).rstrip() + '\n'


def loopback_listener(port: int) -> bool:
    completed = run_command(['ss', '-lntH', f'sport = :{port}'], check=False, capture=True)
    if completed.returncode != 0:
        return False
    addresses: list[str] = []
    for line in completed.stdout.splitlines():
        fields = line.split()
        if len(fields) >= 4:
            addresses.append(fields[3])
    if not addresses:
        return False
    return all(
        address.startswith(('127.0.0.1:', '[::1]:', 'localhost:'))
        for address in addresses
    )


def validate_mongodb(log_path: pathlib.Path) -> None:
    if shutil.which('mongod') is None or shutil.which('mongosh') is None:
        raise BuildError('MongoDB binaries are missing')
    if not MONGODB_CONFIG.is_file() or MONGODB_CONFIG.is_symlink():
        raise BuildError('unsafe or missing /etc/mongod.conf')
    current = MONGODB_CONFIG.read_text(encoding='utf-8')
    if harden_mongod_config(current) != current:
        raise BuildError('mongod.conf is not in HostPanel hardened form')
    run_command(['systemctl', 'is-active', '--quiet', 'mongod.service'], log_path=log_path)
    if not loopback_listener(27017):
        raise BuildError('MongoDB port 27017 is not loopback-only')
    run_command([
        'mongosh', '--quiet', '--host', '127.0.0.1',
        '--eval', 'quit(db.adminCommand({ping:1}).ok ? 0 : 2)',
    ], log_path=log_path)


def apply_mongodb(
    options: dict[str, str], platform: Platform, log_path: pathlib.Path,
    backup_dir: pathlib.Path,
) -> None:
    require_root()
    if options.get('mongodb') == 'off':
        run_command(
            ['systemctl', 'disable', '--now', 'mongod.service'],
            check=False, log_path=log_path,
        )
        return
    if options.get('mongodb') != MONGODB_VERSION:
        raise BuildError('mongodb must be off or 8.0')
    mongodb_supported(platform)
    snapshot = snapshot_paths(
        'mongodb',
        (MONGODB_CONFIG, MONGODB_APT_KEY, MONGODB_APT_LIST, MONGODB_RPM_REPO),
        backup_dir,
    )
    if snapshot:
        print(f'Configuration snapshot: {snapshot}')
    configure_mongodb_repository(platform, log_path)
    refresh_packages(platform, log_path)
    if candidate_version(MONGODB_PACKAGE, platform) is None:
        raise BuildError('MongoDB 8.0 package is unavailable after repository refresh')
    reinstall_packages([MONGODB_PACKAGE], platform, log_path)
    if not MONGODB_CONFIG.is_file() or MONGODB_CONFIG.is_symlink():
        raise BuildError('MongoDB package did not install a safe /etc/mongod.conf')
    updated = harden_mongod_config(MONGODB_CONFIG.read_text(encoding='utf-8'))
    write_atomic_text(MONGODB_CONFIG, updated, stat.S_IMODE(MONGODB_CONFIG.stat().st_mode))
    run_command(['systemctl', 'enable', '--now', 'mongod.service'], log_path=log_path)
    validate_mongodb(log_path)


def varnish_origin_port(options: dict[str, str]) -> int:
    mode = options.get('webserver')
    if mode in {'nginx_apache', 'apache'}:
        return 8080
    if mode == 'openlitespeed':
        return 8088
    raise BuildError('Varnish requires nginx_apache, apache, or openlitespeed web mode')


def render_varnish_vcl(origin_port: int) -> str:
    return f'''vcl 4.1;

import std;

backend default {{
    .host = "127.0.0.1";
    .port = "{origin_port}";
    .connect_timeout = 5s;
    .first_byte_timeout = 120s;
    .between_bytes_timeout = 30s;
}}

acl purge {{
    "127.0.0.1";
    "::1";
}}

sub vcl_recv {{
    if (req.method == "PURGE") {{
        if (client.ip !~ purge) {{ return (synth(405)); }}
        return (purge);
    }}
    if (req.method != "GET" && req.method != "HEAD") {{ return (pass); }}
    if (req.http.Authorization || req.http.Cookie) {{ return (pass); }}
    if (req.url ~ "(?i)^/(wp-admin|wp-login\\.php|admin|api)(/|$)") {{ return (pass); }}
    if (req.http.X-Forwarded-For) {{
        set req.http.X-Forwarded-For = req.http.X-Forwarded-For + ", " + client.ip;
    }} else {{
        set req.http.X-Forwarded-For = client.ip;
    }}
    return (hash);
}}

sub vcl_backend_response {{
    if (beresp.http.Set-Cookie ||
        beresp.http.Cache-Control ~ "(?i)(private|no-cache|no-store)" ||
        beresp.status >= 500) {{
        set beresp.uncacheable = true;
        set beresp.ttl = 120s;
        return (deliver);
    }}
    if (beresp.ttl <= 0s) {{ set beresp.ttl = 120s; }}
    set beresp.grace = 30s;
    set beresp.keep = 60s;
}}

sub vcl_deliver {{
    if (obj.hits > 0) {{
        set resp.http.X-HostPanel-Cache = "HIT";
    }} else {{
        set resp.http.X-HostPanel-Cache = "MISS";
    }}
}}
'''


def varnish_service_user() -> str | None:
    for name in ('varnish', 'vcache'):
        with contextlib.suppress(KeyError):
            pwd.getpwnam(name)
            return name
    return None


def render_varnish_dropin(binary: str, secret: pathlib.Path | None = None) -> str:
    arguments = [
        binary, '-F', '-a', f'127.0.0.1:{VARNISH_PORT}',
        '-T', f'127.0.0.1:{VARNISH_ADMIN_PORT}',
        '-f', str(VARNISH_VCL), '-s', 'malloc,256m',
    ]
    if secret is not None:
        arguments.extend(['-S', str(secret)])
    user = varnish_service_user()
    if user:
        arguments.extend(['-j', f'unix,user={user}'])
    return '[Service]\nExecStart=\nExecStart=' + ' '.join(arguments) + '\n'


def managed_proxy_files() -> list[pathlib.Path]:
    if not NGINX_AVAILABLE.is_dir() or NGINX_AVAILABLE.is_symlink():
        raise BuildError(f'unsafe or missing nginx vhost directory: {NGINX_AVAILABLE}')
    result: list[pathlib.Path] = []
    markers = ('@apache', '# HostPanel Apache-only edge', 'OpenLiteSpeed is deliberately private')
    for path in sorted(NGINX_AVAILABLE.iterdir()):
        if path.is_symlink() or not path.is_file():
            continue
        text = path.read_text(encoding='utf-8')
        if any(marker in text for marker in markers):
            result.append(path)
    return result


def rewrite_varnish_proxies(enable: bool, origin_port: int, log_path: pathlib.Path) -> None:
    files = managed_proxy_files()
    saved: dict[pathlib.Path, str] = {}
    direct = f'proxy_pass http://127.0.0.1:{origin_port};'
    cached = f'proxy_pass http://127.0.0.1:{VARNISH_PORT};'
    marker = f'# HostPanel Varnish origin {origin_port}'
    try:
        for path in files:
            original = path.read_text(encoding='utf-8')
            saved[path] = original
            if enable:
                if direct not in original and cached not in original:
                    raise BuildError(f'cannot locate managed backend in {path}')
                updated = original.replace(direct, cached)
                if marker not in updated:
                    updated = marker + '\n' + updated
            else:
                if cached not in original and direct not in original:
                    raise BuildError(f'cannot locate Varnish backend in {path}')
                updated = original.replace(cached, direct)
                updated = '\n'.join(
                    line for line in updated.splitlines() if line.strip() != marker
                ).rstrip() + '\n'
            metadata = path.lstat()
            write_atomic_bytes(
                path, updated.encode('utf-8'), stat.S_IMODE(metadata.st_mode),
                metadata.st_uid, metadata.st_gid,
            )
        run_command(['nginx', '-t'], log_path=log_path)
        run_command(['systemctl', 'reload', 'nginx.service'], log_path=log_path)
    except Exception:
        for path, original in saved.items():
            metadata = path.lstat()
            write_atomic_bytes(
                path, original.encode('utf-8'), stat.S_IMODE(metadata.st_mode),
                metadata.st_uid, metadata.st_gid,
            )
        run_command(['nginx', '-t'], check=False, log_path=log_path)
        run_command(['systemctl', 'reload', 'nginx.service'], check=False, log_path=log_path)
        raise


def validate_varnish(options: dict[str, str], log_path: pathlib.Path) -> None:
    origin_port = varnish_origin_port(options) if options.get('varnish') == 'on' else (
        8088 if options.get('webserver') == 'openlitespeed' else 8080
    )
    mode = VARNISH_MODE_FILE.read_text(encoding='ascii').strip() \
        if VARNISH_MODE_FILE.is_file() else 'off'
    if mode != options.get('varnish'):
        raise BuildError('Varnish runtime mode does not match build.conf')
    if mode == 'off':
        active = run_command(
            ['systemctl', 'is-active', '--quiet', 'varnish.service'],
            check=False, capture=True,
        )
        if active.returncode == 0:
            raise BuildError('Varnish service is active while varnish=off')
        for path in managed_proxy_files():
            if f'proxy_pass http://127.0.0.1:{VARNISH_PORT};' in path.read_text(encoding='utf-8'):
                raise BuildError(f'nginx vhost still points to disabled Varnish: {path}')
        return
    binary = shutil.which('varnishd')
    if binary is None or not VARNISH_VCL.is_file():
        raise BuildError('Varnish runtime is missing')
    run_command([binary, '-C', '-f', str(VARNISH_VCL)], log_path=log_path)
    run_command(['systemctl', 'is-active', '--quiet', 'varnish.service'], log_path=log_path)
    if not loopback_listener(VARNISH_PORT):
        raise BuildError('Varnish port 6081 is not loopback-only')
    direct = f'proxy_pass http://127.0.0.1:{origin_port};'
    cached = f'proxy_pass http://127.0.0.1:{VARNISH_PORT};'
    for path in managed_proxy_files():
        text = path.read_text(encoding='utf-8')
        if cached not in text or direct in text:
            raise BuildError(f'nginx vhost is not routed through Varnish: {path}')


def apply_varnish(
    options: dict[str, str], platform: Platform, log_path: pathlib.Path,
    backup_dir: pathlib.Path,
) -> None:
    require_root()
    enabled = options.get('varnish') == 'on'
    origin_port = varnish_origin_port(options) if enabled else (
        8088 if options.get('webserver') == 'openlitespeed' else 8080
    )
    snapshot = snapshot_paths(
        'varnish',
        (VARNISH_VCL, VARNISH_DROPIN, VARNISH_MODE_FILE, NGINX_AVAILABLE),
        backup_dir,
    )
    if snapshot:
        print(f'Configuration snapshot: {snapshot}')
    if not enabled:
        previous = (
            VARNISH_MODE_FILE.read_text(encoding='ascii').strip()
            if VARNISH_MODE_FILE.is_file() else 'off'
        )
        write_atomic_text(VARNISH_MODE_FILE, 'off\n')
        try:
            rewrite_varnish_proxies(False, origin_port, log_path)
        except Exception:
            write_atomic_text(VARNISH_MODE_FILE, previous + '\n')
            raise
        run_command(
            ['systemctl', 'disable', '--now', 'varnish.service'],
            check=False, log_path=log_path,
        )
        validate_varnish(options, log_path)
        return

    refresh_packages(platform, log_path)
    if candidate_version(VARNISH_PACKAGE, platform) is None:
        raise BuildError('Varnish package is unavailable from configured repositories')
    reinstall_packages([VARNISH_PACKAGE], platform, log_path)
    binary = shutil.which('varnishd')
    if binary is None:
        raise BuildError('varnishd is missing after package installation')
    VARNISH_VCL.parent.mkdir(parents=True, mode=0o755, exist_ok=True)
    write_atomic_text(VARNISH_VCL, render_varnish_vcl(origin_port), 0o644)
    secret_candidates = (
        pathlib.Path('/etc/varnish/secret'), pathlib.Path('/etc/varnish/varnish_secret')
    )
    secret = next((path for path in secret_candidates if path.is_file()), None)
    write_atomic_text(VARNISH_DROPIN, render_varnish_dropin(binary, secret), 0o644)
    run_command([binary, '-C', '-f', str(VARNISH_VCL)], log_path=log_path)
    run_command(['systemctl', 'daemon-reload'], log_path=log_path)
    run_command(['systemctl', 'enable', '--now', 'varnish.service'], log_path=log_path)
    run_command(['systemctl', 'is-active', '--quiet', 'varnish.service'], log_path=log_path)
    if not loopback_listener(VARNISH_PORT):
        raise BuildError('Varnish did not bind exclusively to 127.0.0.1:6081')
    write_atomic_text(VARNISH_MODE_FILE, 'on\n')
    try:
        rewrite_varnish_proxies(True, origin_port, log_path)
        validate_varnish(options, log_path)
    except Exception:
        write_atomic_text(VARNISH_MODE_FILE, 'off\n')
        run_command(
            ['systemctl', 'disable', '--now', 'varnish.service'],
            check=False, log_path=log_path,
        )
        raise
