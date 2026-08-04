#!/usr/bin/env python3
"""Transactional public entrypoint for HostPanel webserver reconciliation."""
from __future__ import annotations

import argparse
import importlib.util
import pathlib
import sys
import types

_IMPL_PATH = pathlib.Path(__file__).with_name('hostpanel_build_web_impl.py')
_SPEC = importlib.util.spec_from_file_location(
    '_hostpanel_build_web_impl', _IMPL_PATH
)
if _SPEC is None or _SPEC.loader is None:
    raise ImportError(f'cannot load webserver implementation: {_IMPL_PATH}')
_IMPL = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _IMPL
_SPEC.loader.exec_module(_IMPL)

_PUBLIC_OVERRIDES = {
    'main', 'activate_openlitespeed', '_restore_service_state',
    '_service_enabled', '_service_enablement_state',
}
for _name in dir(_IMPL):
    if _name not in _PUBLIC_OVERRIDES and _name not in globals():
        globals()[_name] = getattr(_IMPL, _name)


_ENABLED_STATES = {'enabled', 'enabled-runtime'}
_DISABLED_STATES = {
    'disabled', 'static', 'indirect', 'generated', 'transient',
    'masked', 'masked-runtime', 'not-found',
}


def _service_enablement_state(name: str) -> str:
    completed = run(['systemctl', 'is-enabled', name], check=False)
    state = _command_text(completed.stdout).lower()
    error = _command_text(completed.stderr)
    if completed.returncode == 0 and state in _ENABLED_STATES and not error:
        return state
    if (
        completed.returncode in {0, 1, 3, 4}
        and state in _DISABLED_STATES
        and not error
    ):
        return state
    detail = error or state or str(completed.returncode)
    raise BuildError(f'could not determine enabled state for {name}: {detail}')


def _service_enabled(name: str) -> bool:
    return _service_enablement_state(name) in _ENABLED_STATES


def _restore_service_state(
    name: str, was_active: bool, was_enabled: bool,
    *, was_runtime_masked: bool = False,
) -> list[str]:
    """Attempt every boot, runtime, and mask restoration phase."""
    errors: list[str] = []
    commands = [
        ['systemctl', 'enable' if was_enabled else 'disable', name],
        ['systemctl', 'start' if was_active else 'stop', name],
    ]
    if was_runtime_masked:
        commands.append(['systemctl', 'mask', '--runtime', name])
    for command in commands:
        try:
            run(command)
        except Exception as exc:
            errors.append(f"{' '.join(command)}: {exc}")
    return errors


def activate_openlitespeed() -> None:
    name = 'lsws.service'
    initial_enablement = _service_enablement_state(name)
    if initial_enablement == 'masked':
        raise BuildError(
            'OpenLiteSpeed is persistently masked; unmask it explicitly before activation'
        )

    was_runtime_masked = initial_enablement == 'masked-runtime'
    was_active: bool | None = None
    was_enabled: bool | None = None
    try:
        if was_runtime_masked:
            run(['systemctl', 'unmask', '--runtime', name])
        was_active = _service_active(name)
        was_enabled = _service_enabled(name)
        run(['systemctl', 'enable', '--now', name])
        if not _service_active(name):
            raise BuildError('OpenLiteSpeed did not become active')
    except Exception as original_error:
        rollback_errors: list[str] = []
        if was_active is not None and was_enabled is not None:
            rollback_errors.extend(
                _restore_service_state(
                    name,
                    was_active,
                    was_enabled,
                    was_runtime_masked=was_runtime_masked,
                )
            )
        elif was_runtime_masked:
            try:
                run(['systemctl', 'mask', '--runtime', name])
            except Exception as exc:
                rollback_errors.append(
                    f'systemctl mask --runtime {name}: {exc}'
                )
        if rollback_errors:
            raise BuildError(
                'OpenLiteSpeed activation failed and service rollback also failed: '
                + '; '.join(rollback_errors)
            ) from original_error
        raise


_IMPL._service_enablement_state = _service_enablement_state
_IMPL._service_enabled = _service_enabled
_IMPL._restore_service_state = _restore_service_state
_IMPL.activate_openlitespeed = activate_openlitespeed


def _preparation_snapshots() -> list[tuple[pathlib.Path, tuple[object, ...]]]:
    lsphp_link = OLS_ROOT / 'fcgi-bin/lsphp'
    paths = (
        (OLS_MAIN, False),
        (OLS_ADMIN, False),
        (OLS_REGISTRY, False),
        (lsphp_link, True),
        (LSPHP_STATE, False),
    )
    return [
        (path, _snapshot_path(path, allow_symlink=allow_symlink))
        for path, allow_symlink in paths
    ]


def _restore_preparation(
    snapshots: list[tuple[pathlib.Path, tuple[object, ...]]]
) -> list[str]:
    errors: list[str] = []
    for path, snapshot in reversed(snapshots):
        try:
            _restore_path(path, snapshot)
        except Exception as exc:
            errors.append(f'{path}: {exc}')
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=tuple(MODE_MAP))
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args(argv)
    options = read_config(DEFAULT_CONFIG)
    if options['webserver'] != args.mode:
        raise BuildError('build.conf webserver value changed during reconciliation')

    target = MODE_MAP[args.mode]
    domains = managed_domains()
    if args.check:
        if args.mode == 'openlitespeed':
            check_openlitespeed(options)
        mismatches = [name for name in domains if webserver.mode_of(name) != target]
        if mismatches:
            print('\n'.join(mismatches))
            return 10
        print(f'All {len(domains)} managed domains use {args.mode}.')
        return 0

    preparation: list[tuple[pathlib.Path, tuple[object, ...]]] | None = None
    if args.mode == 'openlitespeed':
        preparation = _preparation_snapshots()
        prepare_openlitespeed(options)

    admin: dict[str, object] = {'role': 'admin', 'user_id': 0, 'username': 'root'}
    previous = {domain: webserver.mode_of(domain) for domain in domains}
    changed: list[str] = []
    try:
        for domain in domains:
            result = webserver.set_mode(domain, target, admin)
            if result.get('changed'):
                changed.append(domain)
        mismatches = [name for name in domains if webserver.mode_of(name) != target]
        if mismatches:
            raise BuildError(
                'post-switch validation failed for: ' + ', '.join(mismatches[:10])
            )
        if args.mode == 'openlitespeed':
            activate_openlitespeed()
    except Exception as original_error:
        rollback_errors = rollback_domains(changed, previous, admin)
        if preparation is not None:
            rollback_errors.extend(_restore_preparation(preparation))
        if rollback_errors:
            raise BuildError(
                f'webserver switch failed ({original_error}); rollback also failed: '
                + '; '.join(rollback_errors)
            ) from original_error
        raise BuildError(
            f'webserver switch failed and was rolled back: {original_error}'
        ) from original_error

    print(f'Applied {args.mode} to {len(domains)} domains ({len(changed)} changed).')
    return 0


class _ForwardingModule(types.ModuleType):
    """Keep test/runtime monkeypatches visible inside the preserved implementation."""

    def __setattr__(self, name: str, value) -> None:
        super().__setattr__(name, value)
        if name in _PUBLIC_OVERRIDES:
            return
        implementation = super().__getattribute__('_IMPL')
        if hasattr(implementation, name):
            setattr(implementation, name, value)


_module = sys.modules.get(__name__)
if _module is not None:
    _module.__class__ = _ForwardingModule


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except BuildError as exc:
        print(f'hostpanel-build-web failed: {exc}', file=sys.stderr)
        raise SystemExit(1)
