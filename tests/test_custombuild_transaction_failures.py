from __future__ import annotations

import pathlib
import subprocess
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
sys.path.insert(0, str(TOOLS))

import hostpanel_build_config as CONFIG
import hostpanel_build_extras_state as STATE
import hostpanel_build_powerdns_adapter as POWERDNS


class CustomBuildTransactionFailureTests(unittest.TestCase):
    def test_state_mask_failure_restarts_previously_active_service(self):
        commands: list[list[str]] = []

        def command(argv, **kwargs):
            commands.append(argv)
            if argv == ['systemctl', 'mask', '--runtime', 'mongod.service']:
                raise OSError('mask failed')
            return subprocess.CompletedProcess(argv, 0, '', '')

        with mock.patch.object(STATE, '_service_active', return_value=True), \
             mock.patch.object(STATE, '_unmask_service') as unmask, \
             mock.patch.object(STATE, 'run_command', side_effect=command):
            with self.assertRaisesRegex(OSError, 'mask failed'):
                STATE._mask_service('mongod.service', pathlib.Path('/tmp/log'))
        self.assertIn(['systemctl', 'stop', 'mongod.service'], commands)
        self.assertIn(['systemctl', 'start', 'mongod.service'], commands)
        unmask.assert_called_once_with('mongod.service', pathlib.Path('/tmp/log'))

    def test_core_mask_adapter_restarts_previously_active_service(self):
        commands: list[list[str]] = []

        def command(argv, **kwargs):
            commands.append(argv)
            if argv == ['systemctl', 'mask', '--runtime', 'pdns.service']:
                raise CONFIG.BuildError('mask failed')
            return subprocess.CompletedProcess(argv, 0, '', '')

        with mock.patch.object(
            POWERDNS.operations, 'service_active', return_value=True
        ), mock.patch.object(POWERDNS, 'run_command', side_effect=command):
            with self.assertRaisesRegex(CONFIG.BuildError, 'mask failed'):
                POWERDNS.mask_service('pdns.service', pathlib.Path('/tmp/log'))
        self.assertIn(['systemctl', 'stop', 'pdns.service'], commands)
        self.assertIn(['systemctl', 'unmask', '--runtime', 'pdns.service'], commands)
        self.assertIn(['systemctl', 'start', 'pdns.service'], commands)

    def test_mongodb_disable_restores_mode_and_service_on_state_write_failure(self):
        options = dict(CONFIG.DEFAULT_OPTIONS)
        restored_modes: list[str] = []

        def write_mode(path, text, *args):
            if text == 'off\n':
                raise OSError('mode write failed')
            restored_modes.append(text)

        with mock.patch.object(STATE, '_runtime_mode', return_value='8.0'), \
             mock.patch.object(STATE, '_service_active', return_value=True), \
             mock.patch.object(STATE, '_service_enabled', return_value=False), \
             mock.patch.object(STATE.base, 'write_atomic_text', side_effect=write_mode), \
             mock.patch.object(STATE, '_restore_service_state') as restore, \
             mock.patch.object(
                 STATE, 'run_command',
                 return_value=subprocess.CompletedProcess([], 0, '', ''),
             ):
            with self.assertRaisesRegex(OSError, 'mode write failed'):
                STATE.apply_mongodb(
                    options, mock.Mock(), pathlib.Path('/tmp/log'),
                    pathlib.Path('/tmp/backup'),
                )
        self.assertEqual(restored_modes, ['8.0\n'])
        restore.assert_called_once_with(
            'mongod.service', True, False, pathlib.Path('/tmp/log')
        )

    def test_restore_service_state_preserves_active_but_disabled(self):
        commands: list[list[str]] = []
        with mock.patch.object(STATE, '_unmask_service'), mock.patch.object(
            STATE, 'run_command',
            side_effect=lambda command, **kwargs: commands.append(command)
            or subprocess.CompletedProcess(command, 0, '', ''),
        ):
            STATE._restore_service_state(
                'varnish.service', True, False, pathlib.Path('/tmp/log')
            )
        self.assertEqual(
            commands,
            [
                ['systemctl', 'disable', 'varnish.service'],
                ['systemctl', 'start', 'varnish.service'],
            ],
        )

    def test_restore_service_state_preserves_enabled_but_inactive(self):
        commands: list[list[str]] = []
        with mock.patch.object(STATE, '_unmask_service'), mock.patch.object(
            STATE, 'run_command',
            side_effect=lambda command, **kwargs: commands.append(command)
            or subprocess.CompletedProcess(command, 0, '', ''),
        ):
            STATE._restore_service_state(
                'mongod.service', False, True, pathlib.Path('/tmp/log')
            )
        self.assertEqual(commands, [['systemctl', 'enable', 'mongod.service']])


if __name__ == '__main__':
    unittest.main()
