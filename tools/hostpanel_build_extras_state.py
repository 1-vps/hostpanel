"""Hardened loader for optional-service runtime state and rollback."""
from __future__ import annotations

import importlib.util
import os
import pathlib
import stat
import sys
import types

_IMPL_PATH = pathlib.Path(__file__).with_name(
    'hostpanel_build_extras_state_impl.py'
)
_SPEC = importlib.util.spec_from_file_location(
    '_hostpanel_build_extras_state_impl', _IMPL_PATH
)
if _SPEC is None or _SPEC.loader is None:
    raise ImportError(f'cannot load extras state implementation: {_IMPL_PATH}')
_IMPL = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _IMPL
_SPEC.loader.exec_module(_IMPL)

BuildError = _IMPL.BuildError
run_command = _IMPL.run_command
_ORIGINAL_ATTEMPT = _IMPL._attempt
_ORIGINAL_FINISH_ROLLBACK = _IMPL._finish_rollback
_ROLLBACK_GUARDS: dict[int, bool] = {}

LegacyCapturedFile = tuple[bytes, int, int, int]
CapturedFile = tuple[bytes, int, int, int, dict[str, bytes]]


def _service_active(unit: str) -> bool:
    completed = run_command(
        ['systemctl', 'is-active', unit], check=False, capture=True
    )
    state = str(getattr(completed, 'stdout', '') or '').strip().lower()
    error = str(getattr(completed, 'stderr', '') or '').strip()
    if completed.returncode == 0 and state == 'active' and not error:
        return True
    if (
        completed.returncode in {3, 4}
        and state in {'inactive', 'failed', 'unknown', 'not-found'}
        and not error
    ):
        return False
    raise BuildError(
        f'could not determine active state for {unit}: '
        f'{error or state or completed.returncode}'
    )


def _service_enabled(unit: str) -> bool:
    completed = run_command(
        ['systemctl', 'is-enabled', unit], check=False, capture=True
    )
    state = str(getattr(completed, 'stdout', '') or '').strip().lower()
    error = str(getattr(completed, 'stderr', '') or '').strip()
    if completed.returncode == 0 and state == 'enabled' and not error:
        return True
    if state in {
        'disabled', 'static', 'indirect', 'generated', 'transient',
        'masked', 'masked-runtime', 'alias', 'not-found',
    } and completed.returncode in {0, 1, 3, 4} and not error:
        return False
    raise BuildError(
        f'could not determine enabled state for {unit}: '
        f'{error or state or completed.returncode}'
    )


def _capture(path: pathlib.Path) -> CapturedFile | None:
    if not os.path.lexists(path):
        return None
    metadata = _IMPL._trusted_config(path)
    xattrs = _IMPL.base._capture_xattrs(path)
    return (
        path.read_bytes(), stat.S_IMODE(metadata.st_mode),
        metadata.st_uid, metadata.st_gid, xattrs,
    )


def _restore(
    path: pathlib.Path,
    captured: CapturedFile | LegacyCapturedFile | None,
) -> None:
    if captured is None:
        path.unlink(missing_ok=True)
        return
    if len(captured) == 4:
        payload, mode, uid, gid = captured
        _IMPL.base.write_atomic_bytes(path, payload, mode, uid, gid)
        return
    payload, mode, uid, gid, xattrs = captured
    _IMPL.base.write_atomic_bytes(path, payload, mode, uid, gid)
    _IMPL.base._apply_xattrs(path, xattrs)


def _attempt(errors: list[str], label: str, function, *args, **kwargs) -> bool:
    key = id(errors)
    if label.startswith('candidate ') and label.endswith(' stop'):
        result = _ORIGINAL_ATTEMPT(
            errors, label, function, *args, **kwargs
        )
        _ROLLBACK_GUARDS[key] = result
        return result
    if _ROLLBACK_GUARDS.get(key) is False:
        return False
    return _ORIGINAL_ATTEMPT(errors, label, function, *args, **kwargs)


def _finish_rollback(
    label: str, original_error: Exception, errors: list[str]
) -> None:
    try:
        _ORIGINAL_FINISH_ROLLBACK(label, original_error, errors)
    finally:
        _ROLLBACK_GUARDS.pop(id(errors), None)


for _name in dir(_IMPL):
    if _name not in {
        '_service_active', '_service_enabled', '_capture', '_restore',
        '_attempt', '_finish_rollback',
    } and _name not in globals():
        globals()[_name] = getattr(_IMPL, _name)

_IMPL._service_active = _service_active
_IMPL._service_enabled = _service_enabled
_IMPL._capture = _capture
_IMPL._restore = _restore
_IMPL._attempt = _attempt
_IMPL._finish_rollback = _finish_rollback

apply_mongodb = _IMPL.apply_mongodb
apply_varnish = _IMPL.apply_varnish
validate_mongodb = _IMPL.validate_mongodb
validate_varnish = _IMPL.validate_varnish
ensure_safe_web_switch = _IMPL.ensure_safe_web_switch
install = _IMPL.install


class _ForwardingModule(types.ModuleType):
    def __setattr__(self, name: str, value) -> None:
        super().__setattr__(name, value)
        implementation = super().__getattribute__('_IMPL')
        if hasattr(implementation, name):
            setattr(implementation, name, value)


_module = sys.modules.get(__name__)
if _module is not None:
    _module.__class__ = _ForwardingModule
