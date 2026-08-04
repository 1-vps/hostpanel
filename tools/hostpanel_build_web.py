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

for _name in dir(_IMPL):
    if _name not in {'main', '_restore_service_state'} and _name not in globals():
        globals()[_name] = getattr(_IMPL, _name)


def _restore_service_state(
    name: str, was_active: bool, was_enabled: bool
) -> list[str]:
    """Attempt both boot and runtime restoration phases after any operation error."""
    errors: list[str] = []
    commands = (
        ['systemctl', 'enable' if was_enabled else 'disable', name],
        ['systemctl', 'start' if was_active else 'stop', name],
    )
    for command in commands:
        try:
            run(command)
        except Exception as exc:
            errors.append(f"{' '.join(command)}: {exc}")
    return errors


_IMPL._restore_service_state = _restore_service_state


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
        if name in {'main', '_restore_service_state'}:
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
