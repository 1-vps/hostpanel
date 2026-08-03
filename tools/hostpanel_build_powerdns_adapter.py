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
    include_dir.mkdir(parents=True, mode=0o755, exist_ok=True)
    trusted_root_directory(include_dir)
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


def trusted_root_directory(path: pathlib.Path) -> None:
    metadata = path.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise BuildError(f'unsafe PowerDNS include directory: {path}')


def trusted_root_file(path: pathlib.Path) -> None:
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
    trusted_root_directory(include_dir)
    trusted_root_file(target)
    return include_dir, target


def dns_requested(component: str, roles: set[str]) -> bool:
    return component == 'dns' or (component == 'all' and 'dns' in roles)


def guarded_apply_build(
    original: Callable, component, options, platform, log_path, backup_dir,
    python_path, doctor_path, roles, web_helper, mode_file,
):
    watcher = operations.PDNS_PATH_UNIT.name
    watcher_was_active = (
        operations.service_active(watcher) if dns_requested(component, roles) else False
    )
    try:
        return original(
            component, options, platform, log_path, backup_dir,
            python_path, doctor_path, roles, web_helper, mode_file,
        )
    except Exception:
        if watcher_was_active:
            run_command(
                ['systemctl', 'enable', '--now', watcher],
                check=False, log_path=log_path,
            )
        raise


def install() -> None:
    operations.powerdns_include_dir = powerdns_include_dir
    operations.launch_values = launch_values
    operations.active_setting_keys = active_setting_keys

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
