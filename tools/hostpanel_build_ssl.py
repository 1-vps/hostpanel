"""Free SSL certificate lifecycle helpers for hostpanel-build."""
from __future__ import annotations

import contextlib
import os
import pathlib
import re
import secrets
import shutil
import stat
import tempfile
from collections.abc import Iterator

import hostpanel_build_config as config
from hostpanel_build_config import BuildError, owner_ids
from hostpanel_build_operations import run_command

DOMAIN_RE = re.compile(
    r'^(?=.{1,253}$)(?!-)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+'
    r'[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?$'
)
EMAIL_RE = re.compile(r'^[A-Za-z0-9.!#$%&\'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$')
EAB_RE = re.compile(r'^[A-Za-z0-9._~+/-]{8,2048}$')
PROVIDERS = {'letsencrypt', 'zerossl'}
ZEROSSL_SERVER = 'https://acme.zerossl.com/v2/DV90'
DEFAULT_NGINX_AVAILABLE = pathlib.Path('/etc/nginx/hostpanel-sites')
DEFAULT_NGINX_ENABLED = pathlib.Path('/etc/nginx/hostpanel-enabled')
DEFAULT_LIVE_ROOT = pathlib.Path('/etc/letsencrypt/live')
DEFAULT_HOOK = pathlib.Path('/etc/letsencrypt/renewal-hooks/deploy/hostpanel-reload-nginx')
DEFAULT_EAB_KID_FILE = pathlib.Path('/etc/hostpanel/zerossl-eab-kid')
DEFAULT_EAB_HMAC_FILE = pathlib.Path('/etc/hostpanel/zerossl-eab-hmac')
DEFAULT_RUNTIME_DIR = pathlib.Path('/run/hostpanel-build')
HOOK_TEXT = """#!/usr/bin/env bash
set -euo pipefail
nginx -t
systemctl reload nginx.service
"""


def validate_domain(value: str) -> str:
    domain = value.strip().lower().rstrip('.')
    if DOMAIN_RE.fullmatch(domain) is None:
        raise BuildError('invalid certificate domain')
    return domain


def validate_email(value: str) -> str:
    email = value.strip()
    if len(email) > 254 or EMAIL_RE.fullmatch(email) is None:
        raise BuildError('invalid certificate email')
    return email


def validate_provider(value: str) -> str:
    provider = value.strip().lower()
    if provider not in PROVIDERS:
        raise BuildError(
            f"unsupported certificate provider: {provider or 'empty'}"
        )
    return provider


def _safe_root_directory(path: pathlib.Path, mode: int) -> None:
    uid, gid = owner_ids()
    if not path.is_absolute():
        raise BuildError(f'unsafe runtime directory path: {path}')
    if not os.path.lexists(path):
        path.mkdir(parents=True, mode=mode)
        os.chown(path, uid, gid)
        os.chmod(path, mode)
    metadata = path.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != uid
        or metadata.st_gid != gid
        or stat.S_IMODE(metadata.st_mode) != mode
    ):
        raise BuildError(f'unsafe runtime directory: {path}')


def ensure_certbot() -> str:
    certbot = shutil.which('certbot')
    if certbot is None:
        raise BuildError(
            'Certbot is not installed. Install the certbot and '
            'python3-certbot-nginx packages before applying SSL changes.'
        )
    return certbot


def require_managed_vhost(
    domain: str,
    available_root: pathlib.Path = DEFAULT_NGINX_AVAILABLE,
    enabled_root: pathlib.Path = DEFAULT_NGINX_ENABLED,
) -> pathlib.Path:
    domain = validate_domain(domain)
    available = available_root / domain
    enabled = enabled_root / domain
    if not available.is_file():
        raise BuildError(f'HostPanel nginx vhost is missing: {available}')
    if not enabled.is_symlink():
        raise BuildError(f'HostPanel nginx vhost is not enabled: {enabled}')
    try:
        enabled_target = enabled.resolve(strict=True)
        available_target = available.resolve(strict=True)
    except OSError as exc:
        raise BuildError(f'could not resolve managed nginx vhost: {domain}') from exc
    if enabled_target != available_target:
        raise BuildError(f'nginx vhost symlink does not match the managed file: {domain}')
    return available


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
        raise BuildError(
            f'could not capture Certbot hook metadata: {exc}'
        ) from exc


def _apply_xattrs(path: pathlib.Path, values: dict[str, bytes]) -> None:
    try:
        existing = set(os.listxattr(path, follow_symlinks=False))
        desired = set(values)
        for name in existing - desired:
            os.removexattr(path, name, follow_symlinks=False)
        for name, value in values.items():
            os.setxattr(path, name, value, follow_symlinks=False)
    except OSError as exc:
        raise BuildError(
            f'could not preserve Certbot hook metadata: {exc}'
        ) from exc


def _apply_expected_selinux_context(
    fd: int, path: pathlib.Path, mode: int
) -> None:
    config._apply_expected_selinux_context(fd, path, mode)


def _fsync_parent(path: pathlib.Path) -> None:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, 'O_DIRECTORY'):
        flags |= os.O_DIRECTORY
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path.parent, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _open_hook_temporary(path: pathlib.Path) -> tuple[int, pathlib.Path]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    for _ in range(32):
        temporary = path.with_name(
            f'.{path.name}.hostpanel-ssl.{secrets.token_hex(12)}'
        )
        try:
            return os.open(temporary, flags, 0o700), temporary
        except FileExistsError:
            continue
    raise BuildError(f'could not allocate deploy-hook temporary: {path}')


def _enforce_existing_hook_mode(
    path: pathlib.Path, before: os.stat_result
) -> None:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise BuildError(f'Certbot deploy hook changed during validation: {path}')
        if stat.S_IMODE(opened.st_mode) != 0o755:
            os.fchmod(descriptor, 0o755)
            os.fsync(descriptor)
        after = os.fstat(descriptor)
        if stat.S_IMODE(after.st_mode) != 0o755:
            raise BuildError(f'could not enforce Certbot deploy hook mode: {path}')
    finally:
        os.close(descriptor)


def install_deploy_hook(path: pathlib.Path = DEFAULT_HOOK) -> None:
    uid, gid = owner_ids()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    parent = path.parent.lstat()
    if (
        not stat.S_ISDIR(parent.st_mode)
        or stat.S_ISLNK(parent.st_mode)
        or parent.st_uid != uid
        or parent.st_gid != gid
        or stat.S_IMODE(parent.st_mode) & 0o022
    ):
        raise BuildError(f'unsafe Certbot hook directory: {path.parent}')

    existing_xattrs: dict[str, bytes] | None = None
    if os.path.lexists(path):
        metadata = path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != uid
            or metadata.st_gid != gid
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) & 0o022
        ):
            raise BuildError(f'unsafe Certbot deploy hook: {path}')
        try:
            content = path.read_text(encoding='utf-8')
        except (OSError, UnicodeError) as exc:
            raise BuildError(f'could not read Certbot deploy hook: {path}') from exc
        if content == HOOK_TEXT and stat.S_IMODE(metadata.st_mode) == 0o755:
            return
        existing_xattrs = _capture_xattrs(path)

    fd, temporary = _open_hook_temporary(path)
    active_error: BaseException | None = None
    cleanup_error: BaseException | None = None
    try:
        view = memoryview(HOOK_TEXT.encode('utf-8'))
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise BuildError('could not write Certbot deploy hook')
            view = view[written:]
        os.fsync(fd)
        os.fchown(fd, uid, gid)
        os.fchmod(fd, 0o755)
        if existing_xattrs is not None:
            _apply_xattrs(temporary, existing_xattrs)
        else:
            _apply_expected_selinux_context(fd, path, 0o755)
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


def read_eab_secret(path: pathlib.Path, label: str) -> str:
    uid, gid = owner_ids()
    try:
        before = path.lstat()
    except OSError as exc:
        raise BuildError(f'{label} file is missing: {path}') from exc
    if (
        not stat.S_ISREG(before.st_mode)
        or stat.S_ISLNK(before.st_mode)
        or before.st_uid != uid
        or before.st_gid != gid
        or before.st_nlink != 1
        or stat.S_IMODE(before.st_mode) not in {0o400, 0o600}
        or before.st_size < 8
        or before.st_size > 2048
    ):
        raise BuildError(f'unsafe {label} file: {path}')

    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise BuildError(f'could not open {label} file safely: {path}') from exc
    try:
        opened = os.fstat(descriptor)
        if (
            opened.st_dev, opened.st_ino, opened.st_mode,
            opened.st_uid, opened.st_gid, opened.st_nlink, opened.st_size,
        ) != (
            before.st_dev, before.st_ino, before.st_mode,
            before.st_uid, before.st_gid, before.st_nlink, before.st_size,
        ):
            raise BuildError(f'{label} changed before read: {path}')
        chunks: list[bytes] = []
        remaining = opened.st_size
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                raise BuildError(f'{label} ended unexpectedly: {path}')
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise BuildError(f'{label} grew during read: {path}')
        after = os.fstat(descriptor)
        current = path.lstat()
        opened_identity = (
            opened.st_dev, opened.st_ino, opened.st_mode,
            opened.st_uid, opened.st_gid, opened.st_nlink,
            opened.st_size, opened.st_mtime_ns,
        )
        after_identity = (
            after.st_dev, after.st_ino, after.st_mode,
            after.st_uid, after.st_gid, after.st_nlink,
            after.st_size, after.st_mtime_ns,
        )
        current_identity = (
            current.st_dev, current.st_ino, current.st_mode,
            current.st_uid, current.st_gid, current.st_nlink,
            current.st_size, current.st_mtime_ns,
        )
        if opened_identity != after_identity or after_identity != current_identity:
            raise BuildError(f'{label} changed during read: {path}')
        payload = b''.join(chunks)
    except OSError as exc:
        raise BuildError(f'could not read {label} file safely: {path}') from exc
    finally:
        os.close(descriptor)

    try:
        value = payload.decode('ascii', errors='strict').strip()
    except UnicodeError as exc:
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
    active_error: BaseException | None = None
    cleanup_error: BaseException | None = None
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
        descriptor, fd = fd, -1
        os.close(descriptor)
        yield path
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
            path.unlink(missing_ok=True)
        except BaseException as exc:
            if cleanup_error is None:
                cleanup_error = exc
        if active_error is None and cleanup_error is not None:
            raise cleanup_error


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
    install_deploy_hook(hook_path)
    run_command(['nginx', '-t'], log_path=log_path)
    if provider == 'zerossl':
        with zerossl_certbot_config(eab_kid_file, eab_hmac_file, runtime_dir) as config_path:
            run_command(
                _issue_command(certbot, domain, email, include_www, config_path),
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
