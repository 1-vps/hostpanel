from __future__ import annotations

import pathlib
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import hostpanel_build_entry as entry
import hostpanel_build_extras_state as state


class AppliedRuntimeGuardFollowupTests(unittest.TestCase):
    def test_optional_modes_are_checked_without_matching_roles(self) -> None:
        parsed = SimpleNamespace(
            command='doctor', roles_file='/roles', os_release='/os-release'
        )

        def runtime_mode(path, default):
            if path == state.MONGODB_MODE_FILE:
                return state.MONGODB_VERSION
            if path == state.VARNISH_MODE_FILE:
                return 'off'
            raise AssertionError(path)

        with mock.patch.object(entry, 'read_roles', return_value=set()), \
             mock.patch.object(
                 entry.cli, 'detect_platform'
             ) as detect_platform, \
             mock.patch.object(
                 state, '_runtime_mode', side_effect=runtime_mode
             ), mock.patch.object(
                 entry, '_validate_optional_unit_state'
             ) as validate_optional:
            entry.validate_applied_runtime(parsed)

        detect_platform.assert_not_called()
        self.assertEqual(
            validate_optional.call_args_list,
            [
                mock.call(
                    'mongod.service', active=True, enabled=True
                ),
                mock.call(
                    'varnish.service', active=False, enabled=False
                ),
            ],
        )

    def test_every_doctor_reference_gets_the_runtime_guard(self) -> None:
        parsed = SimpleNamespace(
            command='update_versions',
            roles_file='/roles', os_release='/os-release',
        )
        operations = entry.operations_adapter.operations
        events: list[str] = []

        def cli_doctor(*_args):
            events.append('cli doctor')
            return 'cli result'

        def operations_doctor(*_args):
            events.append('operations doctor')
            return 'operations result'

        with mock.patch.object(
            entry.cli, 'run_doctor', side_effect=cli_doctor
        ) as original_cli, mock.patch.object(
            operations, 'run_doctor', side_effect=operations_doctor
        ) as original_operations, mock.patch.object(
            entry, 'validate_applied_runtime',
            side_effect=lambda value: events.append(
                'validate' if value is parsed else 'wrong parsed value'
            ),
        ):
            with entry._command_runtime_guard(parsed):
                self.assertEqual(
                    entry.cli.run_doctor('/log', '/python', '/doctor'),
                    'cli result',
                )
                self.assertEqual(
                    operations.run_doctor('/log', '/python', '/doctor'),
                    'operations result',
                )
            self.assertIs(entry.cli.run_doctor, original_cli)
            self.assertIs(operations.run_doctor, original_operations)

        self.assertEqual(
            events,
            [
                'cli doctor', 'validate',
                'operations doctor', 'validate',
            ],
        )

    def test_watcher_enablement_is_reset_before_start(self) -> None:
        adapter = entry.powerdns_adapter
        path_service = adapter.operations.PDNS_PATH_UNIT.name
        original = adapter._enable_and_start
        events: list[tuple[str, str]] = []

        def base_enable(name: str, _log_path: pathlib.Path) -> None:
            events.append(('enable', name))

        try:
            adapter._enable_and_start = base_enable
            with mock.patch.object(
                adapter, '_set_enabled',
                side_effect=lambda name, enabled, _log_path: events.append(
                    ('disable' if not enabled else 'set-enabled', name)
                ),
            ):
                entry.install_powerdns_watcher_enablement_guard()
                adapter._enable_and_start(
                    path_service, pathlib.Path('/tmp/log')
                )
                adapter._enable_and_start(
                    'pdns.service', pathlib.Path('/tmp/log')
                )
        finally:
            adapter._enable_and_start = original

        self.assertEqual(
            events,
            [
                ('disable', path_service),
                ('enable', path_service),
                ('enable', 'pdns.service'),
            ],
        )


if __name__ == '__main__':
    unittest.main()
