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

        def cli_main(_values):
            events.append('cli-main')
            return 0

        parsed = SimpleNamespace(command='set', lock_file='/run/custom.lock')
        with mock.patch.object(entry, 'install_runtime_adapters'), \
             mock.patch.object(entry.cli, 'parse_args', return_value=parsed), \
             mock.patch.object(entry.cli, 'acquire_lock', side_effect=lock), \
             mock.patch.object(entry.cli, 'main', side_effect=cli_main):
            self.assertEqual(entry.main(['set', 'dns', 'powerdns']), 0)

        self.assertEqual(events, ['lock-enter', 'cli-main', 'lock-exit'])

    def test_build_relies_on_cli_transaction_lock_without_double_lock(self) -> None:
        parsed = SimpleNamespace(command='build', lock_file='/run/custom.lock')
        with mock.patch.object(entry, 'install_runtime_adapters'), \
             mock.patch.object(entry.cli, 'parse_args', return_value=parsed), \
             mock.patch.object(entry.cli, 'acquire_lock') as outer_lock, \
             mock.patch.object(entry.cli, 'main', return_value=0) as cli_main:
            self.assertEqual(entry.main(['build', 'dns', '--apply']), 0)
        outer_lock.assert_not_called()
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


if __name__ == '__main__':
    unittest.main()
