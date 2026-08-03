"""Durable runtime-state and rollback wrapper for optional CustomBuild extras."""
from __future__ import annotations

import contextlib
import pathlib

import hostpanel_build_extras as base
from hostpanel_build_config import BuildError, Platform
from hostpanel_build_packages import run_command

MONGODB_MODE_FILE = pathlib.Path('/etc/hostpanel/mongodb-mode')
VARNISH_MODE_FILE = base.VARNISH_MODE_FILE
MONGODB_VERSION = base.MONGODB_VERSION

extra_components = base.extra_components
varnish_origin_port = base.varnish_origin_port


def _runtime_mode(path: pathlib.Path, default: str) -> str:
    if not path.is_file():
        return default
    try:
        return path.read_text(encoding='ascii').strip()
    except OSError as exc:
        raise BuildError(f'cannot read runtime mode file: {path}') from exc


def validate_mongodb(options: dict[str, str], log_path: pathlib.Path) -> None:
    configured = options.get('mongodb', 'off')
    runtime = _runtime_mode(MONGODB_MODE_FILE, 'off')
    if runtime != configured:
        raise BuildError('MongoDB runtime mode does not match build.conf')
    if configured == 'off':
        active = run_command(
            ['systemctl', 'is-active', '--quiet', 'mongod.service'],
            check=False, capture=True,
        )
        if active.returncode == 0:
            raise BuildError('mongod.service is active while mongodb=off')
        return
    if configured != MONGODB_VERSION:
        raise BuildError('mongodb must be off or 8.0')
    base.validate_mongodb(log_path)


def apply_mongodb(
    options: dict[str, str], platform: Platform, log_path: pathlib.Path,
    backup_dir: pathlib.Path,
) -> None:
    configured = options.get('mongodb', 'off')
    base.apply_mongodb(options, platform, log_path, backup_dir)
    if configured == 'off':
        base.write_atomic_text(MONGODB_MODE_FILE, 'off\n')
        validate_mongodb(options, log_path)
        return
    try:
        base.write_atomic_text(MONGODB_MODE_FILE, MONGODB_VERSION + '\n')
        validate_mongodb(options, log_path)
    except Exception:
        run_command(
            ['systemctl', 'disable', '--now', 'mongod.service'],
            check=False, log_path=log_path,
        )
        with contextlib.suppress(Exception):
            base.write_atomic_text(MONGODB_MODE_FILE, 'off\n')
        raise


def validate_varnish(options: dict[str, str], log_path: pathlib.Path) -> None:
    base.validate_varnish(options, log_path)


def apply_varnish(
    options: dict[str, str], platform: Platform, log_path: pathlib.Path,
    backup_dir: pathlib.Path,
) -> None:
    origin_port = (
        base.varnish_origin_port(options)
        if options.get('varnish') == 'on'
        else (8088 if options.get('webserver') == 'openlitespeed' else 8080)
    )
    try:
        base.apply_varnish(options, platform, log_path, backup_dir)
    except Exception:
        # The base transaction restores files when nginx rejects a rewrite. A
        # later validation failure also needs an explicit direct-origin repair
        # before Varnish remains stopped.
        with contextlib.suppress(Exception):
            base.write_atomic_text(VARNISH_MODE_FILE, 'off\n')
            base.rewrite_varnish_proxies(False, origin_port, log_path)
        run_command(
            ['systemctl', 'disable', '--now', 'varnish.service'],
            check=False, log_path=log_path,
        )
        raise
