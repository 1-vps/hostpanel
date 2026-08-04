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
import hostpanel_build_entry as ENTRY
import hostpanel_build_extras_state as STATE
import hostpanel_build_powerdns_adapter as POWERDNS


class CustomBuildTransactionFailureTests(unittest.TestCase):
    def test_candidate_stop_guard_blocks_all_later_rollback_mutations(self):
        ENTRY.install_optional_rollback_guard()
        for label, unit in (
            ('candidate MongoDB stop', 'mongod.service'),
            ('candidate Varnish stop', 'varnish.service'),
        ):
            with self.subTest(label=label):
                errors: list[str] = []
                stop = mock.Mock(
                    side_effect=CONFIG.BuildError('candidate still active')
                )
                later = mock.Mock()
                with mock.patch.object(STATE, '_disable_now', stop):
                    self.assertFalse(STATE._attempt(
                        errors, label, STATE._disable_now,
                        unit, pathlib.Path('/tmp/log'),
                    ))
                stop.assert_called_once_with(
                    unit, pathlib.Path('/tmp/log'), allow_absent=True
                )
                self.assertRegex(errors[0], '^' + label + ':')
                self.assertFalse(STATE._attempt(
                    errors, 'configuration restore', later,
                    pathlib.Path('/tmp/config'), None,
                ))
                later.assert_not_called()

    def test_candidate_stop_guard_tolerates_only_absent_units(self):
        ENTRY.install_optional_rollback_guard()
        stop = mock.Mock(return_value=None)
        errors: list[str] = []
        with mock.patch.object(STATE, '_disable_now', stop):
            self.assertTrue(STATE._attempt(
                errors, 'candidate MongoDB stop', STATE._disable_now,
                'mongod.service', pathlib.Path('/tmp/log'),
            ))
        stop.assert_called_once_with(
            'mongod.service', pathlib.Path('/tmp/log'), allow_absent=True
        )
        self.assertEqual(errors, [])

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
        self.assertIn(
            ['systemctl', 'is-active', '--quiet', 'mongod.service'], commands
        )
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

    def test_mongodb_disable_restores_service_without_rewriting_unchanged_mode(self):
        options = dict(CONFIG.DEFAULT_OPTIONS)
        writes: list[str] = []

        def write_mode(path, text, *args):
            writes.append(text)
            if text == 'off\n':
                raise OSError('mode write failed')

        with mock.patch.object(STATE, '_runtime_mode', return_value='8.0'), \
             mock.patch.object(
                 STATE, '_service_active', return_value=True
             ), mock.patch.object(STATE, '_service_enabled', return_value=False), \
             mock.patch.object(
                 STATE.base, 'write_atomic_text', side_effect=write_mode
             ), mock.patch.object(
                 STATE, '_restore_service_state'
             ) as restore, mock.patch.object(
                 STATE, 'run_command',
                 return_value=subprocess.CompletedProcess([], 0, '', ''),
             ):
            with self.assertRaisesRegex(OSError, 'mode write failed'):
                STATE.apply_mongodb(
                    options, mock.Mock(), pathlib.Path('/tmp/log'),
                    pathlib.Path('/tmp/backup'),
                )
        self.assertEqual(writes, ['off\n'])
        restore.assert_called_once_with(
            'mongod.service', True, False, pathlib.Path('/tmp/log')
        )

    def test_mongodb_disable_reports_service_rollback_failure(self):
        options = dict(CONFIG.DEFAULT_OPTIONS)

        with mock.patch.object(STATE, '_runtime_mode', return_value='8.0'), \
             mock.patch.object(
                 STATE, '_service_active', return_value=True
             ), mock.patch.object(STATE, '_service_enabled', return_value=False), \
             mock.patch.object(
                 STATE.base, 'write_atomic_text',
                 side_effect=OSError('mode write failed'),
             ), mock.patch.object(
                 STATE, '_restore_service_state',
                 side_effect=CONFIG.BuildError('service restore failed'),
             ), mock.patch.object(
                 STATE, 'run_command',
                 return_value=subprocess.CompletedProcess([], 0, '', ''),
             ):
            with self.assertRaisesRegex(
                CONFIG.BuildError,
                'MongoDB disable failed and rollback also failed.*service restore failed',
            ) as raised:
                STATE.apply_mongodb(
                    options, mock.Mock(), pathlib.Path('/tmp/log'),
                    pathlib.Path('/tmp/backup'),
                )
        self.assertIsInstance(raised.exception.__cause__, OSError)

    def test_varnish_disable_reports_proxy_and_service_rollback_failures(self):
        options = dict(CONFIG.DEFAULT_OPTIONS)
        runtime_modes = iter(['nginx_apache', 'on', 'off'])

        def rewrite(enabled, *args):
            if not enabled:
                raise OSError('direct proxy restore failed')

        with mock.patch.object(
                 STATE, '_runtime_mode', side_effect=lambda *args: next(runtime_modes)
             ), mock.patch.object(STATE.base, 'require_root'), \
             mock.patch.object(STATE.base, 'snapshot_paths', return_value=None), \
             mock.patch.object(STATE, '_capture', return_value=None), \
             mock.patch.object(STATE, '_service_active', return_value=True), \
             mock.patch.object(STATE, '_service_enabled', return_value=True), \
             mock.patch.object(STATE.base, 'write_atomic_text'), \
             mock.patch.object(
                 STATE.base, 'rewrite_varnish_proxies', side_effect=rewrite
             ), mock.patch.object(
                 STATE, '_restore_service_state',
                 side_effect=CONFIG.BuildError('service restore failed'),
             ):
            with self.assertRaisesRegex(
                CONFIG.BuildError,
                'Varnish disable failed and rollback also failed.*service restore failed',
            ) as raised:
                STATE.apply_varnish(
                    options, mock.Mock(), pathlib.Path('/tmp/log'),
                    pathlib.Path('/tmp/backup'),
                )
        self.assertIsInstance(raised.exception.__cause__, OSError)

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
                ['systemctl', 'is-active', '--quiet', 'varnish.service'],
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

    def test_restore_service_state_surfaces_start_failure(self):
        def command(argv, **kwargs):
            if argv == ['systemctl', 'start', 'varnish.service']:
                return subprocess.CompletedProcess(argv, 1, '', 'permission denied')
            return subprocess.CompletedProcess(argv, 0, '', '')

        with mock.patch.object(STATE, '_unmask_service'), mock.patch.object(
            STATE, 'run_command', side_effect=command
        ):
            with self.assertRaisesRegex(
                CONFIG.BuildError, 'systemctl start varnish.service failed'
            ):
                STATE._restore_service_state(
                    'varnish.service', True, False, pathlib.Path('/tmp/log')
                )


if __name__ == '__main__':
    unittest.main()
