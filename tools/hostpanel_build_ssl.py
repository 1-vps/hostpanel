"""Free Let's Encrypt certificate operations for hostpanel-build."""
from __future__ import annotations

import os
import pathlib
import re
import shutil
import stat

from hostpanel_build_config import BuildError, owner_ids
from hostpanel_build_packages import run_command

DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)
EMAIL_RE = re.compile(r"^[^\s@]{1,64}@[^\s@]{1,190}$")
DEFAULT_NGINX_AVAILABLE = pathlib.Path('/etc/nginx/sites-available')
DEFAULT_NGINX_ENABLED = pathlib.Path('/etc/nginx/sites-enabled')
DEFAULT_LIVE_ROOT = pathlib.Path('/etc/letsencrypt/live')
DEFAULT_HOOK = pathlib.Path(
    '/etc/letsencrypt/renewal-hooks/deploy/hostpanel-reload-nginx'
)
HOOK_TEXT = """#!/usr/bin/env bash
set -Eeuo pipefail
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH
nginx -t
systemctl reload nginx.service
"""


def validate_domain(value: str) -> str:
    domain = value.strip().lower().rstrip('.')
    if DOMAIN_RE.fullmatch(domain) is None or domain.endswith('.localdomain'):
        raise BuildError(f'invalid certificate domain: {value}')
    return domain


def validate_email(value: str) -> str:
    email = value.strip()
    if len(email) > 254 or EMAIL_RE.fullmatch(email) is None:
        raise BuildError(f'invalid certificate email address: {value}')
    return email


def _trusted_regular_file(path: pathlib.Path, *, executable: bool = False) -> None:
    uid, gid = owner_ids()
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise BuildError(f'missing managed nginx vhost: {path}') from exc
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


def _safe_hook_parent(path: pathlib.Path) -> None:
    uid, gid = owner_ids()
    path.parent.mkdir(parents=True, mode=0o755, exist_ok=True)
    metadata = path.parent.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != uid
        or metadata.st_gid != gid
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise BuildError(f'unsafe Certbot deploy-hook directory: {path.parent}')


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


def print_ssl_plan(
    action: str, domain: str | None = None, email: str | None = None,
    include_www: bool = False,
) -> None:
    print("HostPanel free SSL plan")
    print(f"  action: {action}")
    if domain:
        print(f"  domain: {validate_domain(domain)}")
    if email:
        print(f"  email:  {validate_email(email)}")
    if action == 'issue':
        print(f"  names:  {domain}" + (f", www.{domain}" if include_www else ""))
        print("  issuer: Let's Encrypt through Certbot's nginx plugin")
        print("  result: HTTPS installation, HTTP redirect, automatic renewal hook")
    elif action == 'renew':
        print("  result: renew due certificates and reload nginx after deployment")
    print('No changes are made without --apply.')


def issue_certificate(
    domain: str, email: str, include_www: bool, log_path: pathlib.Path,
    *, available_root: pathlib.Path = DEFAULT_NGINX_AVAILABLE,
    enabled_root: pathlib.Path = DEFAULT_NGINX_ENABLED,
    live_root: pathlib.Path = DEFAULT_LIVE_ROOT,
    hook_path: pathlib.Path = DEFAULT_HOOK,
) -> None:
    domain = validate_domain(domain)
    email = validate_email(email)
    require_managed_vhost(domain, available_root, enabled_root)
    certbot = ensure_certbot()
    run_command(['nginx', '-t'], log_path=log_path)
    command = [
        certbot, '--nginx', '--non-interactive', '--agree-tos',
        '--email', email, '--redirect', '--keep-until-expiring',
        '--cert-name', domain, '-d', domain,
    ]
    if include_www:
        command.extend(['-d', f'www.{domain}'])
    run_command(command, log_path=log_path)
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
