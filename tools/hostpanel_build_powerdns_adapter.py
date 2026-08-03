"""Runtime compatibility adapter for PowerDNS BIND-backend operation."""
from __future__ import annotations

import os
import pathlib
import stat
from collections.abc import Callable

import hostpanel_build_operations as operations
from hostpanel_build_config import BuildError, Platform
from hostpanel_build_packages import run_command


def setting_pairs(text: str) -> list[tuple[str, bool, str]]:
    result: list[tuple[str, bool, str]] = []
    for raw in text.splitlines():
        line = raw.split('#', 1)[0].strip()
        if not line or '=' not in line:
            continue
        raw_key, value = line.split('=', 1)
        key = raw_key.strip()
        append = key.endswith('+')
        key = key[:-1].rstrip() if append else key
        result.append((key, append, value.strip()))
    return result


def trusted_root_directory(path: pathlib.Path) -> None:
    metadata = path.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise BuildError(f'unsafe PowerDNS include directory: {path}')


def trusted_root_directory_chain(path: pathlib.Path) -> None:
    current = path
    while True:
        trusted_root_directory(current)
        if current.parent == current:
            return
        current = current.parent


def prepare_include_directory(path: pathlib.Path) -> None:
    existing = path
    while not os.path.lexists(existing):
        if existing.parent == existing:
            raise BuildError(f'cannot resolve PowerDNS include directory parent: {path}')
        existing = existing.parent
    trusted_root_directory_chain(existing)
    path.mkdir(parents=True, mode=0o755, exist_ok=True)
    trusted_root_directory_chain(path)


def powerdns_include_dir(native: pathlib.Path) -> pathlib.Path:
    text = native.read_text(encoding='utf-8')
    configured = [
        pathlib.Path(value) if pathlib.Path(value).is_absolute()
        else native.parent / value
        for key, _append, value in setting_pairs(text)
        if key == 'include-dir' and value
    ]
    unique = list(dict.fromkeys(configured))
    if len(unique) > 1:
        raise BuildError('PowerDNS has multiple conflicting include-dir settings')
    include_dir = unique[0] if unique else native.parent / 'pdns.d'
    if not unique:
        suffix = '' if text.endswith('\n') or not text else '\n'
        operations.write_atomic_root(
            native, text + suffix + f'include-dir={include_dir}\n', 0o640
        )
    prepare_include_directory(include_dir)
    return include_dir


def launch_values(text: str) -> set[str]:
    values: set[str] = set()
    for key, _append, value in setting_pairs(text):
        if key != 'launch':
            continue
        values.update(
            item.strip().split(':', 1)[0]
            for item in value.split(',')
            if item.strip()
        )
    return values


def active_setting_keys(text: str) -> set[str]:
    return {key for key, _append, _value in setting_pairs(text)}


def trusted_root_file(path: pathlib.Path) -> None:
    trusted_root_directory_chain(path.parent)
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise BuildError(f'unsafe PowerDNS backend configuration: {path}')


def readable_backend_paths() -> tuple[pathlib.Path, pathlib.Path]:
    native = operations.native_powerdns_config()
    include_dir = operations.powerdns_include_dir(native)
    target = operations.select_powerdns_backend_config(
        native, include_dir, include_dir / operations.PDNS_DROPIN_NAME
    )
    trusted_root_directory_chain(include_dir)
    trusted_root_file(target)
    return include_dir, target


def dns_requested(component: str, roles: set[str]) -> bool:
    return component == 'dns' or (component == 'all' and 'dns' in roles)


def applied_dns_mode() -> str:
    try:
        mode = operations.DNS_MODE_FILE.read_text(encoding='ascii').strip()
    except OSError:
        mode = 'bind'
    if mode not in {'bind', 'powerdns'}:
        raise BuildError('invalid applied HostPanel DNS mode')
    return mode


def reconcile_dns_services(
    options: dict[str, str], platform: Platform, log_path: pathlib.Path
) -> None:
    mode = options['dns']
    if mode not in {'bind', 'powerdns'}:
        raise BuildError('invalid selected HostPanel DNS mode')
    _, _, _, bind_service = operations.dns_layout(platform)
    pdns_service = 'pdns.service'
    path_service = operations.PDNS_PATH_UNIT.name
    target = pdns_service if mode == 'powerdns' else bind_service
    other = bind_service if mode == 'powerdns' else pdns_service
    other_was_active = operations.service_active(other)
    path_was_active = operations.service_active(path_service)

    run_command(['systemctl', 'stop', path_service], check=False, log_path=log_path)
    run_command(['systemctl', 'stop', other], check=False, log_path=log_path)
    operations.unmask_service(target, log_path)
    try:
        run_command(['systemctl', 'enable', '--now', target], log_path=log_path)
        run_command(['systemctl', 'is-active', '--quiet', target], log_path=log_path)
        if mode == 'powerdns':
            run_command(['pdns_control', 'rediscover'], log_path=log_path)
            run_command(['pdns_control', 'reload'], log_path=log_path)
            run_command(
                ['systemctl', 'enable', '--now', path_service], log_path=log_path
            )
            run_command(
                ['systemctl', 'is-active', '--quiet', path_service], log_path=log_path
            )
        operations.persist_dns_mode(mode)
    except Exception:
        run_command(['systemctl', 'stop', target], check=False, log_path=log_path)
        if other_was_active:
            run_command(
                ['systemctl', 'enable', '--now', other],
                check=False, log_path=log_path,
            )
        if path_was_active and other == pdns_service:
            run_command(
                ['systemctl', 'enable', '--now', path_service],
                check=False, log_path=log_path,
            )
        raise
    run_command(['systemctl', 'disable', other], check=False, log_path=log_path)
    if mode == 'bind':
        run_command(
            ['systemctl', 'disable', path_service], check=False, log_path=log_path
        )


def guarded_apply_build(
    original: Callable, component, options, platform, log_path, backup_dir,
    python_path, doctor_path, roles, web_helper, mode_file,
):
    requested = dns_requested(component, roles)
    previous_mode = applied_dns_mode() if requested else None
    watcher = operations.PDNS_PATH_UNIT.name
    watcher_was_active = operations.service_active(watcher) if requested else False
    try:
        return original(
            component, options, platform, log_path, backup_dir,
            python_path, doctor_path, roles, web_helper, mode_file,
        )
    except Exception as original_error:
        rollback_error: Exception | None = None
        if requested and previous_mode is not None:
            try:
                current_mode = applied_dns_mode()
            except Exception:
                current_mode = None
            if current_mode != previous_mode:
                rollback_options = dict(options)
                rollback_options['dns'] = previous_mode
                try:
                    operations.reconcile_dns_services(
                        rollback_options, platform, log_path
                    )
                except Exception as exc:
                    rollback_error = exc
        if watcher_was_active and previous_mode == 'powerdns':
            run_command(
                ['systemctl', 'enable', '--now', watcher],
                check=False, log_path=log_path,
            )
        if rollback_error is not None:
            raise BuildError(
                f'build failed and DNS rollback also failed: {rollback_error}'
            ) from original_error
        raise


def install() -> None:
    operations.powerdns_include_dir = powerdns_include_dir
    operations.launch_values = launch_values
    operations.active_setting_keys = active_setting_keys
    operations.reconcile_dns_services = reconcile_dns_services

    original = operations.configure_powerdns
    if getattr(original, '_hostpanel_readable_backend', False) is True:
        return

    def configure(platform: Platform, log_path: pathlib.Path) -> None:
        original(platform, log_path)
        include_dir, target = readable_backend_paths()
        os.chown(include_dir, 0, 0)
        os.chmod(include_dir, 0o755)
        os.chown(target, 0, 0)
        os.chmod(target, 0o644)

    configure._hostpanel_readable_backend = True  # type: ignore[attr-defined]
    operations.configure_powerdns = configure
