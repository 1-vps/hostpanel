from __future__ import annotations

import contextlib
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
from hostpanel_build_config import BuildError


class CustomBuildSetLockTests(unittest.TestCase):
    def test_set_holds_lock_while_cli_reads_and_writes_config(self) -> None:
        events: list[str] = []

        @contextlib.contextmanager
        def lock(_path: pathlib.Path):
            events.append('lock-enter')
            try:
                yield
            finally:
                events.append('lock-exit')

        original_lock = entry.cli.acquire_lock

        def cli_main(_values):
            events.append('cli-main')
            self.assertIs(entry.cli.acquire_lock, entry._already_locked)
            return 0

        parsed = SimpleNamespace(command='set', lock_file='/run/custom.lock')
        with mock.patch.object(entry, 'install_runtime_adapters'), \
             mock.patch.object(entry.cli, 'parse_args', return_value=parsed), \
             mock.patch.object(entry.cli, 'acquire_lock', side_effect=lock) as acquire, \
             mock.patch.object(entry.cli, 'main', side_effect=cli_main):
            patched_lock = entry.cli.acquire_lock
            self.assertEqual(entry.main(['set', 'dns', 'powerdns']), 0)
            self.assertIs(entry.cli.acquire_lock, patched_lock)

        self.assertEqual(events, ['lock-enter', 'cli-main', 'lock-exit'])
        acquire.assert_called_once_with(pathlib.Path('/run/custom.lock'))
        self.assertIs(entry.cli.acquire_lock, original_lock)

    def test_build_is_locked_before_cli_reads_configuration(self) -> None:
        events: list[str] = []

        @contextlib.contextmanager
        def lock(_path: pathlib.Path):
            events.append('lock-enter')
            try:
                yield
            finally:
                events.append('lock-exit')

        def cli_main(_values):
            events.append('cli-main')
            self.assertIs(entry.cli.acquire_lock, entry._already_locked)
            return 0

        parsed = SimpleNamespace(
            command='build', apply=True, lock_file='/run/custom.lock'
        )
        with mock.patch.object(entry, 'install_runtime_adapters'), \
             mock.patch.object(entry.cli, 'parse_args', return_value=parsed), \
             mock.patch.object(entry.cli, 'acquire_lock', side_effect=lock), \
             mock.patch.object(entry.cli, 'main', side_effect=cli_main):
            self.assertEqual(entry.main(['build', 'dns', '--apply']), 0)

        self.assertEqual(events, ['lock-enter', 'cli-main', 'lock-exit'])

    def test_read_only_options_preserves_non_root_installed_layout(self) -> None:
        parsed = SimpleNamespace(command='options', lock_file='/run/custom.lock')
        with mock.patch.object(entry, 'install_runtime_adapters'), \
             mock.patch.object(entry.cli, 'parse_args', return_value=parsed), \
             mock.patch.object(entry.cli, 'acquire_lock') as acquire, \
             mock.patch.object(entry.cli, 'main', return_value=0) as cli_main:
            self.assertEqual(entry.main(['--allow-non-root', 'options']), 0)
        acquire.assert_not_called()
        cli_main.assert_called_once()

    def test_lock_failure_is_reported_as_a_controlled_cli_error(self) -> None:
        parsed = SimpleNamespace(command='set', lock_file='/run/custom.lock')
        with mock.patch.object(entry, 'install_runtime_adapters'), \
             mock.patch.object(entry.cli, 'parse_args', return_value=parsed), \
             mock.patch.object(
                 entry.cli,
                 'acquire_lock',
                 side_effect=BuildError('another operation is running'),
             ), mock.patch.object(entry.cli, 'main') as cli_main, \
             mock.patch('builtins.print') as printer:
            self.assertEqual(entry.main(['set', 'dns', 'bind']), 1)
        cli_main.assert_not_called()
        self.assertIn('another operation is running', printer.call_args.args[0])

    def test_inner_lock_adapter_is_restored_after_cli_failure(self) -> None:
        @contextlib.contextmanager
        def lock(_path: pathlib.Path):
            yield

        parsed = SimpleNamespace(
            command='build', apply=True, lock_file='/run/custom.lock'
        )
        with mock.patch.object(entry, 'install_runtime_adapters'), \
             mock.patch.object(entry.cli, 'parse_args', return_value=parsed), \
             mock.patch.object(entry.cli, 'acquire_lock', side_effect=lock):
            patched_lock = entry.cli.acquire_lock
            with mock.patch.object(
                entry.cli, 'main', side_effect=OSError('execution failed')
            ):
                self.assertEqual(entry.main(['build', 'dns', '--apply']), 1)
            self.assertIs(entry.cli.acquire_lock, patched_lock)

    def test_lock_selection_covers_every_transactional_command(self) -> None:
        cases = (
            (SimpleNamespace(command='set'), True),
            (SimpleNamespace(command='validate'), True),
            (SimpleNamespace(command='doctor'), True),
            (SimpleNamespace(command='build', apply=True), True),
            (SimpleNamespace(command='build', apply=False), False),
            (SimpleNamespace(command='update_versions', apply=True), True),
            (SimpleNamespace(command='update_panel', apply=True), True),
            (SimpleNamespace(command='ssl', ssl_command='issue', apply=True), True),
            (SimpleNamespace(command='ssl', ssl_command='status'), False),
            (SimpleNamespace(command='options'), False),
        )
        for parsed, expected in cases:
            with self.subTest(parsed=parsed):
                self.assertIs(entry._requires_outer_lock(parsed), expected)


if __name__ == '__main__':
    unittest.main()
