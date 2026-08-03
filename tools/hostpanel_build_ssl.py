"""Free ACME certificate operations for hostpanel-build."""
from __future__ import annotations

import contextlib
import ipaddress
import os
import pathlib
import re
import shutil
import stat
import tempfile
from collections.abc import Iterator

from hostpanel_build_config import BuildError, owner_ids
from hostpanel_build_packages import run_command

DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)
EMAIL_RE = re.compile(r"^[^\s@]{1,64}@[^\s@]{1,190}$")
EAB_RE = re.compile(r'^[A-Za-z0-9_-]{8,1024}$')
PROVIDERS = {'letsencrypt', 'zerossl'}
ZEROSSL_SERVER = 'https://acme.zerossl.com/v2/DV90'
DEFAULT_NGINX_AVAILABLE = pathlib.Path('/etc/nginx/sites-available')
DEFAULT_NGINX_ENABLED = pathlib.Path('/etc/nginx/sites-enabled')
DEFAULT_LIVE_ROOT = pathlib.Path('/etc/letsencrypt/live')
DEFAULT_HOOK = pathlib.Path(
    '/etc/letsencrypt/renewal-hooks/deploy/hostpanel-reload-nginx'
)
DEFAULT_EAB_KID_FILE = pathlib.Path('/etc/hostpanel/ssl/zerossl-eab-kid')
DEFAULT_EAB_HMAC_FILE = pathlib.Path('/etc/hostpanel/ssl/zerossl-eab-hmac')
DEFAULT_RUNTIME_DIR = pathlib.Path('/run/hostpanel-build')
HOOK_TEXT = """#!/usr/bin/env bash
set -Eeuo pipefail
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH
nginx -t
systemctl reload nginx.service
"""


def validate_domain(value: str) -> str:
    domain = value.strip().lower().rstrip('.')
    try:
        ipaddress.ip_address(domain)
    except ValueError:
        pass
    else:
        raise BuildError(f'certificate domain must not be an IP address: {value}')
    if DOMAIN_RE.fullmatch(domain) is None or domain.endswith('.localdomain'):
        raise BuildError(f'invalid certificate domain: {value}')
    return domain


def validate_email(value: str) -> str:
    email = value.strip()
    if len(email) > 254 or EMAIL_RE.fullmatch(email) is None:
        raise BuildError(f'invalid certificate email address: {value}')
    return email


def validate_provider(value: str) -> str:
    provider = value.strip().lower()
    if provider not in PROVIDERS:
        raise BuildError(f"SSL provider must be one of: {', '.join(sorted(PROVIDERS))}")
    return provider


def _trusted_regular_file(path: pathlib.Path, *, executable: bool = False) -> None:
    uid, gid = owner_ids()
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise BuildError(f'missing managed file: {path}') from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != uid
        or metadata.st_gid != gid
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or (executable and not os.access(path, os.X_OK))
    ):
        raise BuildError(f'unsafe managed file: {path}')


def require_managed_vhost(
    domain: str,
    available_root: pathlib.Path = DEFAULT_NGINX_AVAILABLE,
    enabled_root: pathlib.Path = DEFAULT_NGINX_ENABLED,
) -> pathlib.Path:
    domain = validate_domain(domain)
    vhost = available_root / domain
    _trusted_regular_file(vhost)
    enabled = enabled_root / domain
    if not enabled.exists():
        raise BuildError(f'nginx vhost is not enabled for {domain}')
    try:
        target = enabled.resolve(strict=True)
        expected = vhost.resolve(strict=True)
    except OSError as exc:
        raise BuildError(f'could not resolve enabled nginx vhost for {domain}') from exc
    if target != expected:
        raise BuildError(f'enabled nginx vhost does not reference the managed file for {domain}')
    return vhost


def ensure_certbot() -> str:
    binary = shutil.which('certbot')
    if binary is None:
        raise BuildError('certbot is unavailable; rebuild the web component first')
    return binary


def _safe_root_directory(path: pathlib.Path, mode: int) -> None:
    uid, gid = owner_ids()
    path.mkdir(parents=True, mode=mode, exist_ok=True)
    metadata = path.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != uid
        or metadata.st_gid != gid
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise BuildError(f'unsafe SSL directory: {path}')
    os.chmod(path, mode)


def _safe_hook_parent(path: pathlib.Path) -> None:
    _safe_root_directory(path.parent, 0o755)


def install_deploy_hook(path: pathlib.Path = DEFAULT_HOOK) -> None:
    uid, gid = owner_ids()
    _safe_hook_parent(path)
    if path.exists() or path.is_symlink():
        metadata = path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != uid
            or metadata.st_gid != gid
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) & 0o022
        ):
            raise BuildError(f'unsafe existing Certbot deploy hook: {path}')
        if path.read_text(encoding='utf-8') == HOOK_TEXT:
            os.chmod(path, 0o755)
            return
    temporary = path.with_name(f'.{path.name}.{os.getpid()}')
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    fd = os.open(temporary, flags, 0o755)
    try:
        payload = HOOK_TEXT.encode('utf-8')
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise BuildError('could not write Certbot deploy hook')
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)
    os.chown(temporary, uid, gid)
    os.chmod(temporary, 0o755)
    os.replace(temporary, path)


def read_eab_secret(path: pathlib.Path, label: str) -> str:
    uid, gid = owner_ids()
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise BuildError(f'{label} file is missing: {path}') from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != uid
        or metadata.st_gid != gid
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) not in {0o400, 0o600}
        or metadata.st_size < 8
        or metadata.st_size > 2048
    ):
        raise BuildError(f'unsafe {label} file: {path}')
    try:
        value = path.read_bytes().decode('ascii', errors='strict').strip()
    except (OSError, UnicodeError) as exc:
        raise BuildError(f'could not read {label} file safely: {path}') from exc
    if EAB_RE.fullmatch(value) is None:
        raise BuildError(f'{label} contains an invalid value')
    return value


@contextlib.contextmanager
def zerossl_certbot_config(
    kid_file: pathlib.Path, hmac_file: pathlib.Path,
    runtime_dir: pathlib.Path = DEFAULT_RUNTIME_DIR,
) -> Iterator[pathlib.Path]:
    uid, gid = owner_ids()
    kid = read_eab_secret(kid_file, 'ZeroSSL EAB KID')
    hmac = read_eab_secret(hmac_file, 'ZeroSSL EAB HMAC')
    _safe_root_directory(runtime_dir, 0o700)
    fd, name = tempfile.mkstemp(prefix='zerossl-', suffix='.ini', dir=runtime_dir)
    path = pathlib.Path(name)
    try:
        os.fchmod(fd, 0o600)
        os.fchown(fd, uid, gid)
        payload = (
            f'server = {ZEROSSL_SERVER}\n'
            f'eab-kid = {kid}\n'
            f'eab-hmac-key = {hmac}\n'
        ).encode('ascii')
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise BuildError('could not write the temporary ZeroSSL configuration')
            view = view[written:]
        os.fsync(fd)
        os.close(fd)
        fd = -1
        yield path
    finally:
        if fd >= 0:
            os.close(fd)
        with contextlib.suppress(FileNotFoundError):
            path.unlink()


def print_ssl_plan(
    action: str, domain: str | None = None, email: str | None = None,
    include_www: bool = False, provider: str = 'letsencrypt',
) -> None:
    selected_provider = validate_provider(provider) if action == 'issue' else None
    print('HostPanel free SSL plan')
    print(f'  action:   {action}')
    if selected_provider is not None:
        print(f'  provider: {selected_provider}')
    elif action == 'renew':
        print('  provider: stored renewal configuration')
    if domain:
        print(f'  domain:   {validate_domain(domain)}')
    if email:
        print(f'  email:    {validate_email(email)}')
    if action == 'issue':
        print(f'  names:    {domain}' + (f', www.{domain}' if include_www else ''))
        if selected_provider == 'zerossl':
            print('  account:  ZeroSSL ACME with root-protected EAB credentials')
        print('  result:   HTTPS installation, HTTP redirect, automatic renewal hook')
    elif action == 'renew':
        print('  result:   renew due certificates and reload nginx after deployment')
    print('No changes are made without --apply.')


def _issue_command(
    certbot: str, domain: str, email: str, include_www: bool,
    config_path: pathlib.Path | None,
) -> list[str]:
    command = [certbot]
    if config_path is not None:
        command.extend(['--config', str(config_path)])
    command.extend([
        '--nginx', '--non-interactive', '--agree-tos',
        '--email', email, '--redirect', '--keep-until-expiring',
        '--cert-name', domain, '-d', domain,
    ])
    if include_www:
        command.extend(['-d', f'www.{domain}'])
    return command


def issue_certificate(
    domain: str, email: str, include_www: bool, log_path: pathlib.Path,
    *, provider: str = 'letsencrypt',
    eab_kid_file: pathlib.Path = DEFAULT_EAB_KID_FILE,
    eab_hmac_file: pathlib.Path = DEFAULT_EAB_HMAC_FILE,
    available_root: pathlib.Path = DEFAULT_NGINX_AVAILABLE,
    enabled_root: pathlib.Path = DEFAULT_NGINX_ENABLED,
    live_root: pathlib.Path = DEFAULT_LIVE_ROOT,
    hook_path: pathlib.Path = DEFAULT_HOOK,
    runtime_dir: pathlib.Path = DEFAULT_RUNTIME_DIR,
) -> None:
    domain = validate_domain(domain)
    email = validate_email(email)
    provider = validate_provider(provider)
    require_managed_vhost(domain, available_root, enabled_root)
    certbot = ensure_certbot()
    run_command(['nginx', '-t'], log_path=log_path)
    if provider == 'zerossl':
        with zerossl_certbot_config(eab_kid_file, eab_hmac_file, runtime_dir) as config:
            run_command(
                _issue_command(certbot, domain, email, include_www, config),
                log_path=log_path,
            )
    else:
        run_command(
            _issue_command(certbot, domain, email, include_www, None),
            log_path=log_path,
        )
    lineage = live_root / domain
    for name in ('fullchain.pem', 'privkey.pem'):
        if not (lineage / name).is_file():
            raise BuildError(f'Certbot did not install {lineage / name}')
    install_deploy_hook(hook_path)
    run_command(['nginx', '-t'], log_path=log_path)
    run_command(['systemctl', 'reload', 'nginx.service'], log_path=log_path)


def renew_certificates(
    domain: str | None, log_path: pathlib.Path,
    *, hook_path: pathlib.Path = DEFAULT_HOOK,
) -> None:
    certbot = ensure_certbot()
    install_deploy_hook(hook_path)
    command = [certbot, 'renew']
    if domain:
        command.extend(['--cert-name', validate_domain(domain)])
    run_command(command, log_path=log_path)
    run_command(['nginx', '-t'], log_path=log_path)


def certificate_status(domain: str | None) -> int:
    certbot = ensure_certbot()
    command = [certbot, 'certificates']
    if domain:
        command.extend(['--cert-name', validate_domain(domain)])
    completed = run_command(command, check=False, capture=True)
    if completed.stdout:
        print(completed.stdout, end='' if completed.stdout.endswith('\n') else '\n')
    if completed.stderr and completed.returncode != 0:
        print(completed.stderr, end='' if completed.stderr.endswith('\n') else '\n')
    return completed.returncode
