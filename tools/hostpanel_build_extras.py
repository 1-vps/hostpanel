"""Hardened loader for optional CustomBuild extras."""
from __future__ import annotations

import importlib.util
import os
import pathlib
import shutil
import stat
import sys

_IMPL_PATH = pathlib.Path(__file__).with_name('hostpanel_build_extras_impl.py')
_SPEC = importlib.util.spec_from_file_location(
    '_hostpanel_build_extras_impl', _IMPL_PATH
)
if _SPEC is None or _SPEC.loader is None:
    raise ImportError(f'cannot load extras implementation: {_IMPL_PATH}')
_IMPL = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _IMPL
_SPEC.loader.exec_module(_IMPL)

BuildError = _IMPL.BuildError
VARNISH_MODE_FILE = _IMPL.VARNISH_MODE_FILE


def _trusted_varnish_mode(default: str = 'off') -> str:
    path = VARNISH_MODE_FILE
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return default
    except OSError as exc:
        raise BuildError(f'cannot inspect Varnish runtime mode: {path}') from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise BuildError(f'unsafe Varnish runtime mode file: {path}')
    try:
        mode = path.read_text(encoding='ascii').strip()
    except (OSError, UnicodeError) as exc:
        raise BuildError(f'cannot read Varnish runtime mode: {path}') from exc
    if mode not in {'off', 'on'}:
        raise BuildError('invalid Varnish runtime mode')
    return mode


def validate_varnish(options: dict[str, str], log_path: pathlib.Path) -> None:
    origin_port = _IMPL.varnish_origin_port(options) \
        if options.get('varnish') == 'on' else (
            8088 if options.get('webserver') == 'openlitespeed' else 8080
        )
    mode = _trusted_varnish_mode()
    if mode != options.get('varnish'):
        raise BuildError('Varnish runtime mode does not match build.conf')
    if mode == 'off':
        active = _IMPL.run_command(
            ['systemctl', 'is-active', '--quiet', 'varnish.service'],
            check=False, capture=True,
        )
        if active.returncode == 0:
            raise BuildError('Varnish service is active while varnish=off')
        for path in _IMPL.managed_proxy_files():
            if (
                f'proxy_pass http://127.0.0.1:{_IMPL.VARNISH_PORT};'
                in path.read_text(encoding='utf-8')
            ):
                raise BuildError(
                    f'nginx vhost still points to disabled Varnish: {path}'
                )
        return
    binary = shutil.which('varnishd')
    if binary is None or not _IMPL.VARNISH_VCL.is_file():
        raise BuildError('Varnish runtime is missing')
    _IMPL.run_command(
        [binary, '-C', '-f', str(_IMPL.VARNISH_VCL)], log_path=log_path
    )
    _IMPL.run_command(
        ['systemctl', 'is-active', '--quiet', 'varnish.service'],
        log_path=log_path,
    )
    if not _IMPL.loopback_listener(_IMPL.VARNISH_PORT):
        raise BuildError('Varnish port 6081 is not loopback-only')
    direct = f'proxy_pass http://127.0.0.1:{origin_port};'
    cached = f'proxy_pass http://127.0.0.1:{_IMPL.VARNISH_PORT};'
    for path in _IMPL.managed_proxy_files():
        text = path.read_text(encoding='utf-8')
        if cached not in text or direct in text:
            raise BuildError(f'nginx vhost is not routed through Varnish: {path}')


for _name in dir(_IMPL):
    if _name not in {'validate_varnish'} and _name not in globals():
        globals()[_name] = getattr(_IMPL, _name)

_IMPL.validate_varnish = validate_varnish
