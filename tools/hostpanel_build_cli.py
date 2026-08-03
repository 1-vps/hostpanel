"""Command-line interface for HostPanel CustomBuild-style maintenance."""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from typing import Sequence

from hostpanel_build_config import (
    BuildError, DEFAULT_BACKUP_DIR, DEFAULT_CONFIG, DEFAULT_DOCTOR,
    DEFAULT_LOCK, DEFAULT_LOG, DEFAULT_OPTIONS, DEFAULT_PYTHON,
    DEFAULT_ROLES_FILE, DEFAULT_UPDATER, DEFAULT_VERSION_FILE, DEFAULT_WEB_HELPER,
    Platform, component_packages, detect_platform, expand_component,
    read_config, read_roles, render_config, selected_components,
    validate_value, write_config,
)
from hostpanel_build_operations import (
    acquire_lock, apply_build, apply_updates_and_doctor, require_root,
    run_doctor, validate_component, validate_webserver_mode,
)
from hostpanel_build_packages import check_system_updates, package_states, run_command


def print_plan(
    component: str, options: dict[str, str], platform: Platform,
    roles: set[str] | None = None,
) -> None:
    components = expand_component(component, options, roles)
    print('HostPanel build plan')
    print(f'  platform: {platform.family} ({platform.os_id} {platform.version_id})')
    print(f'  target:   {component}')
    if roles is not None:
        print(f"  roles:    {', '.join(sorted(roles))}")
    for item in components:
        packages = component_packages(item, options, platform)
        print(f'  - {item}')
        if packages:
            print(f"      packages: {', '.join(packages)}")
            print('      actions: metadata refresh, config snapshot, package reinstall, validation, restart')
        elif item == 'panel':
            print('      actions: apply the next signed HostPanel release')
    print('No changes are made without --apply.')


def print_versions(
    options: dict[str, str], platform: Platform, version_file: pathlib.Path,
    as_json: bool, roles: set[str],
) -> None:
    panel = version_file.read_text(encoding='utf-8').strip() if version_file.is_file() else None
    states = package_states(selected_components(options, roles), options, platform)
    if as_json:
        print(json.dumps({
            'panel': panel,
            'platform': {
                'family': platform.family, 'id': platform.os_id,
                'version': platform.version_id,
            },
            'packages': [
                {
                    'component': state.component, 'package': state.package,
                    'installed': state.installed, 'candidate': state.candidate,
                }
                for state in states
            ],
        }, sort_keys=True, indent=2))
        return
    print(f"{'COMPONENT':<16} {'PACKAGE':<30} {'INSTALLED':<24} CANDIDATE")
    print(f"{'panel':<16} {'hostpanel':<30} {(panel or '-'):<24} -")
    for state in states:
        print(
            f'{state.component:<16} {state.package:<30} '
            f"{(state.installed or '-'):<24} {state.candidate or '-'}"
        )


def update_panel(apply: bool, updater: pathlib.Path) -> int:
    if not updater.is_file() or not os.access(updater, os.X_OK):
        raise BuildError(f'signed HostPanel updater is unavailable: {updater}')
    if apply:
        require_root()
    completed = run_command([str(updater), '--apply' if apply else '--check'], check=False)
    if completed.returncode not in {0, 10}:
        raise BuildError(f'signed HostPanel updater failed with exit status {completed.returncode}')
    return completed.returncode


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog='hostpanel-build',
        description='CustomBuild-style HostPanel service maintenance with explicit apply gates.',
    )
    parser.add_argument('--config', default=str(DEFAULT_CONFIG))
    parser.add_argument('--lock-file', default=str(DEFAULT_LOCK))
    parser.add_argument('--log-file', default=str(DEFAULT_LOG))
    parser.add_argument('--backup-dir', default=str(DEFAULT_BACKUP_DIR))
    parser.add_argument('--os-release', default='/etc/os-release')
    parser.add_argument('--version-file', default=str(DEFAULT_VERSION_FILE))
    parser.add_argument('--updater', default=str(DEFAULT_UPDATER))
    parser.add_argument('--doctor', default=str(DEFAULT_DOCTOR))
    parser.add_argument('--roles-file', default=str(DEFAULT_ROLES_FILE))
    parser.add_argument('--python', dest='python_path', default=str(DEFAULT_PYTHON))
    parser.add_argument('--web-helper', default=str(DEFAULT_WEB_HELPER))
    parser.add_argument('--web-mode-file', default='/etc/hostpanel/webserver-mode')
    parser.add_argument('--allow-non-root', action='store_true', help=argparse.SUPPRESS)
    subparsers = parser.add_subparsers(dest='command', required=True)
    subparsers.add_parser('options', help='show active build options')
    setter = subparsers.add_parser('set', help='set one build option')
    setter.add_argument('key')
    setter.add_argument('value')
    versions = subparsers.add_parser('versions', help='show installed and candidate versions')
    versions.add_argument('--json', action='store_true')
    plan = subparsers.add_parser('plan', help='show a component maintenance plan')
    plan.add_argument('component', nargs='?', default='all')
    build = subparsers.add_parser('build', help='reinstall and validate a component')
    build.add_argument('component', nargs='?', default='all')
    build.add_argument('--apply', action='store_true')
    validate = subparsers.add_parser('validate', help='validate configurations without reinstalling')
    validate.add_argument('component', nargs='?', default='all')
    update_versions = subparsers.add_parser(
        'update_versions', help='check or apply operating-system package updates'
    )
    update_versions.add_argument('--apply', action='store_true')
    update_panel_parser = subparsers.add_parser(
        'update_panel', help='check or apply the next signed HostPanel release'
    )
    update_panel_parser.add_argument('--apply', action='store_true')
    subparsers.add_parser('doctor', help='run hostpanel-doctor')
    return parser.parse_args(list(argv))


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if os.geteuid() != 0 and not args.allow_non_root:
        print('hostpanel-build failed: commands must run as root', file=sys.stderr)
        return 1
    try:
        options = read_config(pathlib.Path(args.config))
        if args.command == 'options':
            print(render_config(options), end='')
            return 0
        if args.command == 'set':
            require_root()
            if args.key not in DEFAULT_OPTIONS:
                raise BuildError(f'unknown option: {args.key}')
            options[args.key] = validate_value(args.key, args.value)
            write_config(pathlib.Path(args.config), options)
            print(f'{args.key}={options[args.key]}')
            return 0

        platform = detect_platform(pathlib.Path(args.os_release))
        role_commands = {'versions', 'plan', 'build', 'validate'}
        roles = read_roles(pathlib.Path(args.roles_file)) if args.command in role_commands else set()
        role_arg = roles if getattr(args, 'component', None) == 'all' else None
        if args.command == 'versions':
            print_versions(options, platform, pathlib.Path(args.version_file), args.json, roles)
            return 0
        if args.command == 'plan':
            print_plan(args.component, options, platform, role_arg)
            return 0
        if args.command == 'build' and not args.apply:
            print_plan(args.component, options, platform, role_arg)
            return 0
        if args.command == 'update_versions' and not args.apply:
            return check_system_updates(platform)
        if args.command == 'update_panel' and not args.apply:
            return update_panel(False, pathlib.Path(args.updater))

        log_path = pathlib.Path(args.log_file)
        python_path = pathlib.Path(args.python_path)
        doctor_path = pathlib.Path(args.doctor)
        with acquire_lock(pathlib.Path(args.lock_file)):
            if args.command == 'build':
                apply_build(
                    args.component, options, platform, log_path,
                    pathlib.Path(args.backup_dir), python_path, doctor_path, roles,
                    pathlib.Path(args.web_helper), pathlib.Path(args.web_mode_file),
                )
                print(f'Build completed. Log: {log_path}')
                return 0
            if args.command == 'validate':
                for component in expand_component(args.component, options, role_arg):
                    validate_component(component, options, platform, log_path)
                if args.component == 'web' or (args.component == 'all' and 'web' in roles):
                    validate_webserver_mode(
                        options, pathlib.Path(args.web_helper), python_path, log_path
                    )
                run_doctor(log_path, python_path, doctor_path)
                print(f'Validation completed. Log: {log_path}')
                return 0
            if args.command == 'update_versions':
                apply_updates_and_doctor(platform, log_path, python_path, doctor_path)
                print(f'System package updates completed. Log: {log_path}')
                return 0
            if args.command == 'update_panel':
                return update_panel(True, pathlib.Path(args.updater))
            if args.command == 'doctor':
                run_doctor(log_path, python_path, doctor_path)
                print(f'HostPanel doctor completed. Log: {log_path}')
                return 0
        raise BuildError(f'unsupported command: {args.command}')
    except BuildError as exc:
        print(f'hostpanel-build failed: {exc}', file=sys.stderr)
        return 1
