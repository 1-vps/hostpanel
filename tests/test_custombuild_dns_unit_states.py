from __future__ import annotations

import pathlib
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
sys.path.insert(0, str(TOOLS))

import hostpanel_build_config as CONFIG
import hostpanel_build_powerdns_adapter as ADAPTER


class PowerDnsUnitStateTests(unittest.TestCase):
    platform = CONFIG.Platform('debian', 'ubuntu', '24.04')
    layout = (
        pathlib.Path('/etc/bind/named.conf.local'),
        pathlib.Path('/etc/bind/zones'),
        'bind',
        'bind9.service',
    )

    @staticmethod
    def completed(command: list[str], returncode: int = 0, detail: str = ''):
        return subprocess.CompletedProcess(command, returncode, '', detail)

    def test_state_probe_errors_fail_closed(self):
        failure = subprocess.CompletedProcess(
            ['systemctl'], 1, '', 'Failed to connect to bus'
        )
        with mock.patch.object(ADAPTER, 'run_command', return_value=failure):
            with self.assertRaisesRegex(CONFIG.BuildError, 'active state'):
                ADAPTER.service_active('pdns.service')
            with self.assertRaisesRegex(CONFIG.BuildError, 'enabled state'):
                ADAPTER.service_enabled('pdns.service')

        inactive = subprocess.CompletedProcess(['systemctl'], 3, '', '')
        disabled = subprocess.CompletedProcess(['systemctl'], 1, '', '')
        with mock.patch.object(ADAPTER, 'run_command', return_value=inactive):
            self.assertFalse(ADAPTER.service_active('pdns.service'))
        with mock.patch.object(ADAPTER, 'run_command', return_value=disabled):
            self.assertFalse(ADAPTER.service_enabled('pdns.service'))

    def test_reconciled_mask_token_suppresses_core_fallback_restart(self):
        events: list[list[str]] = []
        with mock.patch.object(
            ADAPTER.operations, 'service_active', return_value=True
        ), mock.patch.object(
            ADAPTER, 'run_command',
            side_effect=lambda command, **kwargs: events.append(command)
            or self.completed(command),
        ):
            token = ADAPTER.mask_service(
                'pdns.service', pathlib.Path('/tmp/log')
            )
        self.assertTrue(token)
        ADAPTER._complete_masked_service('pdns.service')
        self.assertFalse(token)
        self.assertIn(['systemctl', 'stop', 'pdns.service'], events)
        self.assertIn(
            ['systemctl', 'mask', '--runtime', 'pdns.service'], events
        )

    def test_reload_failure_restores_active_but_disabled_bind(self):
        options = dict(CONFIG.DEFAULT_OPTIONS)
        options['dns'] = 'powerdns'
        states = {
            'bind9.service': (True, False),
            'pdns.service': (False, False),
            'hostpanel-pdns-zones.path': (False, False),
        }
        events: list[list[str]] = []

        def command(argv, **kwargs):
            events.append(argv)
            if argv == ['pdns_control', 'reload']:
                raise CONFIG.BuildError('reload failed')
            return self.completed(argv)

        with mock.patch.object(ADAPTER.operations, 'dns_layout', return_value=self.layout), \
             mock.patch.object(
                 ADAPTER.operations, 'service_active',
                 side_effect=lambda unit: states[unit][0],
             ), mock.patch.object(
                 ADAPTER, 'service_enabled',
                 side_effect=lambda unit: states[unit][1],
             ), mock.patch.object(ADAPTER, 'applied_dns_mode', return_value='bind'), \
             mock.patch.object(ADAPTER.operations, 'persist_dns_mode') as persist, \
             mock.patch.object(ADAPTER, 'run_command', side_effect=command):
            with self.assertRaisesRegex(CONFIG.BuildError, 'reload failed'):
                ADAPTER.reconcile_dns_services(
                    options, self.platform, pathlib.Path('/tmp/log')
                )

        self.assertIn(['systemctl', 'disable', 'bind9.service'], events)
        self.assertIn(['systemctl', 'start', 'bind9.service'], events)
        self.assertIn(['systemctl', 'disable', 'pdns.service'], events)
        self.assertNotIn(['systemctl', 'enable', 'bind9.service'], events)
        persist.assert_not_called()

    def test_rollback_stop_failure_does_not_mutate_boot_or_mode_state(self):
        options = dict(CONFIG.DEFAULT_OPTIONS)
        options['dns'] = 'powerdns'
        states = {
            'bind9.service': (True, True),
            'pdns.service': (False, False),
            'hostpanel-pdns-zones.path': (False, False),
        }
        events: list[list[str]] = []

        def command(argv, **kwargs):
            events.append(argv)
            if argv == ['pdns_control', 'reload']:
                raise CONFIG.BuildError('reload failed')
            if argv == ['systemctl', 'stop', 'pdns.service']:
                return self.completed(argv, 1, 'permission denied')
            return self.completed(argv)

        with mock.patch.object(ADAPTER.operations, 'dns_layout', return_value=self.layout), \
             mock.patch.object(
                 ADAPTER.operations, 'service_active',
                 side_effect=lambda unit: states[unit][0],
             ), mock.patch.object(
                 ADAPTER, 'service_enabled',
                 side_effect=lambda unit: states[unit][1],
             ), mock.patch.object(ADAPTER, 'applied_dns_mode', return_value='bind'), \
             mock.patch.object(ADAPTER.operations, 'persist_dns_mode') as persist, \
             mock.patch.object(ADAPTER, 'run_command', side_effect=command):
            with self.assertRaisesRegex(CONFIG.BuildError, 'rollback also failed'):
                ADAPTER.reconcile_dns_services(
                    options, self.platform, pathlib.Path('/tmp/log')
                )

        failure_index = events.index(['systemctl', 'stop', 'pdns.service'])
        after_failure = events[failure_index + 1:]
        self.assertFalse(any(
            len(command) > 1 and command[1] in {
                'enable', 'disable', 'unmask', 'start'
            }
            for command in after_failure
        ))
        persist.assert_not_called()

    def test_obsolete_unit_disable_failure_rolls_back_before_mode_persist(self):
        options = dict(CONFIG.DEFAULT_OPTIONS)
        options['dns'] = 'powerdns'
        states = {
            'bind9.service': (True, True),
            'pdns.service': (False, False),
            'hostpanel-pdns-zones.path': (False, False),
        }
        events: list[list[str]] = []

        def command(argv, **kwargs):
            events.append(argv)
            if argv == ['systemctl', 'disable', 'bind9.service']:
                return self.completed(argv, 1, 'permission denied')
            return self.completed(argv)

        with mock.patch.object(ADAPTER.operations, 'dns_layout', return_value=self.layout), \
             mock.patch.object(
                 ADAPTER.operations, 'service_active',
                 side_effect=lambda unit: states[unit][0],
             ), mock.patch.object(
                 ADAPTER, 'service_enabled',
                 side_effect=lambda unit: states[unit][1],
             ), mock.patch.object(ADAPTER, 'applied_dns_mode', return_value='bind'), \
             mock.patch.object(ADAPTER.operations, 'persist_dns_mode') as persist, \
             mock.patch.object(ADAPTER, 'run_command', side_effect=command):
            with self.assertRaisesRegex(CONFIG.BuildError, 'could not disable bind9'):
                ADAPTER.reconcile_dns_services(
                    options, self.platform, pathlib.Path('/tmp/log')
                )

        self.assertIn(
            ['systemctl', 'enable', '--now', 'bind9.service'], events
        )
        persist.assert_not_called()

    def test_successful_handoff_disables_obsolete_units_before_persisting(self):
        options = dict(CONFIG.DEFAULT_OPTIONS)
        options['dns'] = 'powerdns'
        states = {
            'bind9.service': (True, True),
            'pdns.service': (False, False),
            'hostpanel-pdns-zones.path': (False, False),
        }
        events: list[object] = []

        def command(argv, **kwargs):
            events.append(argv)
            return self.completed(argv)

        def persist(mode: str) -> None:
            events.append(('persist', mode))

        with mock.patch.object(ADAPTER.operations, 'dns_layout', return_value=self.layout), \
             mock.patch.object(
                 ADAPTER.operations, 'service_active',
                 side_effect=lambda unit: states[unit][0],
             ), mock.patch.object(
                 ADAPTER, 'service_enabled',
                 side_effect=lambda unit: states[unit][1],
             ), mock.patch.object(ADAPTER, 'applied_dns_mode', return_value='bind'), \
             mock.patch.object(
                 ADAPTER.operations, 'persist_dns_mode', side_effect=persist
             ), mock.patch.object(ADAPTER, 'run_command', side_effect=command):
            ADAPTER.reconcile_dns_services(
                options, self.platform, pathlib.Path('/tmp/log')
            )

        self.assertLess(
            events.index(['systemctl', 'disable', 'bind9.service']),
            events.index(('persist', 'powerdns')),
        )
        self.assertLess(
            events.index([
                'systemctl', 'enable', '--now', 'hostpanel-pdns-zones.path'
            ]),
            events.index(('persist', 'powerdns')),
        )
        self.assertIn(
            ['systemctl', 'enable', '--now', 'pdns.service'], events
        )

    def test_late_build_failure_restores_exact_boot_and_runtime_states(self):
        previous = {
            'bind9.service': (True, False),
            'pdns.service': (False, True),
            'hostpanel-pdns-zones.path': (False, True),
        }
        changed = {
            'bind9.service': (False, False),
            'pdns.service': (True, True),
            'hostpanel-pdns-zones.path': (True, True),
        }
        events: list[list[str]] = []

        def command(argv, **kwargs):
            events.append(argv)
            return self.completed(argv)

        original = mock.Mock(side_effect=OSError('later component failed'))
        with mock.patch.object(
                 ADAPTER, 'applied_dns_mode', side_effect=['bind', 'powerdns']
             ), mock.patch.object(
                 ADAPTER, '_dns_service_states', side_effect=[previous, changed]
             ), mock.patch.object(
                 ADAPTER.operations, 'dns_layout', return_value=self.layout
             ), mock.patch.object(
                 ADAPTER.operations, 'persist_dns_mode'
             ) as persist, mock.patch.object(
                 ADAPTER, 'run_command', side_effect=command
             ):
            with self.assertRaisesRegex(OSError, 'later component failed'):
                ADAPTER.guarded_apply_build(
                    original, 'all', {'dns': 'powerdns'}, self.platform,
                    pathlib.Path('/tmp/log'), pathlib.Path('/tmp/backup'),
                    pathlib.Path('/tmp/python'), pathlib.Path('/tmp/doctor'),
                    {'dns'}, pathlib.Path('/tmp/web'), pathlib.Path('/tmp/mode'),
                )

        self.assertIn(['systemctl', 'disable', 'bind9.service'], events)
        self.assertIn(['systemctl', 'start', 'bind9.service'], events)
        self.assertIn(['systemctl', 'enable', 'pdns.service'], events)
        self.assertNotIn(['systemctl', 'start', 'pdns.service'], events)
        self.assertIn(['systemctl', 'enable', 'hostpanel-pdns-zones.path'], events)
        self.assertNotIn(['systemctl', 'start', 'hostpanel-pdns-zones.path'], events)
        persist.assert_called_once_with('bind')

    def test_late_non_dns_failure_does_not_restart_unchanged_dns(self):
        states = {
            'bind9.service': (True, True),
            'pdns.service': (False, False),
            'hostpanel-pdns-zones.path': (False, False),
        }
        original = mock.Mock(side_effect=OSError('later component failed'))
        with mock.patch.object(
                 ADAPTER, 'applied_dns_mode', side_effect=['bind', 'bind']
             ), mock.patch.object(
                 ADAPTER, '_dns_service_states', side_effect=[states, states]
             ), mock.patch.object(ADAPTER, 'run_command') as command, \
             mock.patch.object(ADAPTER.operations, 'persist_dns_mode') as persist:
            with self.assertRaisesRegex(OSError, 'later component failed'):
                ADAPTER.guarded_apply_build(
                    original, 'all', {'dns': 'bind'}, self.platform,
                    pathlib.Path('/tmp/log'), pathlib.Path('/tmp/backup'),
                    pathlib.Path('/tmp/python'), pathlib.Path('/tmp/doctor'),
                    {'dns'}, pathlib.Path('/tmp/web'), pathlib.Path('/tmp/mode'),
                )
        command.assert_not_called()
        persist.assert_not_called()

    def test_native_config_append_preserves_mode_owner_and_group(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            native = root / 'pdns.conf'
            native.write_text('guardian=yes\n', encoding='utf-8')
            native.chmod(0o640)
            before = native.stat()
            with mock.patch.object(ADAPTER, 'trusted_root_file'), \
                 mock.patch.object(ADAPTER, 'prepare_include_directory'), \
                 mock.patch.object(ADAPTER.os, 'fchown') as fchown:
                include = ADAPTER.powerdns_include_dir(native)
            after = native.stat()
            updated = native.read_text(encoding='utf-8')
            leftovers = list(root.glob('.pdns.conf.hostpanel-pdns.*'))

            self.assertEqual(include, root / 'pdns.d')
            self.assertEqual(stat.S_IMODE(after.st_mode), 0o640)
            self.assertIn('include-dir=', updated)
            fchown.assert_called_once_with(mock.ANY, before.st_uid, before.st_gid)
            self.assertEqual(leftovers, [])

    def test_failed_native_config_write_removes_temporary_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            native = root / 'pdns.conf'
            native.write_text('guardian=yes\n', encoding='utf-8')
            with mock.patch.object(ADAPTER.os, 'write', return_value=0):
                with self.assertRaisesRegex(CONFIG.BuildError, 'could not write'):
                    ADAPTER.write_atomic_preserving(native, 'replacement\n')
            self.assertEqual(native.read_text(encoding='utf-8'), 'guardian=yes\n')
            self.assertEqual(list(root.glob('.pdns.conf.hostpanel-pdns.*')), [])


if __name__ == '__main__':
    unittest.main()
