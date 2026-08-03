from __future__ import annotations

import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
sys.path.insert(0, str(TOOLS))

import hostpanel_build_config as CONFIG
import hostpanel_build_operations as OPERATIONS


class CustomBuildPowerDnsTests(unittest.TestCase):
    def test_bind_is_default_and_powerdns_is_selectable(self):
        self.assertEqual(CONFIG.DEFAULT_OPTIONS['dns'], 'bind')
        self.assertEqual(CONFIG.validate_value('dns', 'bind'), 'bind')
        self.assertEqual(CONFIG.validate_value('dns', 'powerdns'), 'powerdns')
        with self.assertRaises(CONFIG.BuildError):
            CONFIG.validate_value('dns', 'knot')

    def test_powerdns_package_mapping_is_platform_aware(self):
        options = dict(CONFIG.DEFAULT_OPTIONS)
        options['dns'] = 'powerdns'
        debian = CONFIG.Platform('debian', 'ubuntu', '24.04')
        rhel = CONFIG.Platform('rhel', 'rocky', '9')
        self.assertEqual(
            CONFIG.component_packages('dns', options, debian),
            ['pdns-server', 'pdns-backend-bind'],
        )
        self.assertEqual(CONFIG.component_packages('dns', options, rhel), ['pdns'])
        options['dns'] = 'bind'
        self.assertEqual(
            CONFIG.component_packages('dns', options, debian),
            ['bind9', 'bind9-utils'],
        )
        self.assertEqual(
            CONFIG.component_packages('dns', options, rhel),
            ['bind', 'bind-utils'],
        )

    def test_dns_validation_and_service_follow_selected_mode(self):
        options = dict(CONFIG.DEFAULT_OPTIONS)
        debian = CONFIG.Platform('debian', 'debian', '13')
        self.assertEqual(
            OPERATIONS.validation_commands('dns', options, debian),
            [['named-checkconf']],
        )
        self.assertEqual(OPERATIONS.services_for('dns', options, debian), ['bind9'])
        options['dns'] = 'powerdns'
        self.assertEqual(
            OPERATIONS.validation_commands('dns', options, debian),
            [['pdns_server', '--config=check']],
        )
        self.assertEqual(OPERATIONS.services_for('dns', options, debian), ['pdns'])

    def test_powerdns_uses_existing_hostpanel_zone_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            managed_conf = root / 'named.conf.local'
            zone_dir = root / 'zones'
            native = root / 'powerdns' / 'pdns.conf'
            include_dir = native.parent / 'pdns.d'
            managed_conf.write_text(
                'zone "example.com" { type master; file "example.com.zone"; };\n',
                encoding='utf-8',
            )
            zone_dir.mkdir()
            native.parent.mkdir()
            native.write_text(f'include-dir={include_dir}\n', encoding='utf-8')
            include_dir.mkdir()
            writes: list[tuple[pathlib.Path, str, int]] = []
            commands: list[list[str]] = []
            platform = CONFIG.Platform('debian', 'ubuntu', '24.04')
            with mock.patch.object(OPERATIONS, 'require_root'), \
                 mock.patch.object(
                     OPERATIONS, 'dns_layout',
                     return_value=(managed_conf, zone_dir, 'bind', 'bind9.service'),
                 ), \
                 mock.patch.object(OPERATIONS, 'native_powerdns_config', return_value=native), \
                 mock.patch.object(OPERATIONS, 'powerdns_include_dir', return_value=include_dir), \
                 mock.patch.object(OPERATIONS, 'reject_conflicting_powerdns_backends'), \
                 mock.patch.object(
                     OPERATIONS, 'write_atomic_root',
                     side_effect=lambda path, text, mode=0o644: writes.append((path, text, mode)),
                 ), \
                 mock.patch.object(OPERATIONS, 'install_powerdns_units'), \
                 mock.patch.object(
                     OPERATIONS, 'run_command',
                     side_effect=lambda command, **kwargs: commands.append(command)
                     or subprocess.CompletedProcess(command, 0, '', ''),
                 ):
                OPERATIONS.configure_powerdns(platform, root / 'log')
            dropin = next(text for path, text, _ in writes if path.name == '99-hostpanel.conf')
            self.assertIn('launch=bind', dropin)
            self.assertIn(f'bind-config={managed_conf}', dropin)
            self.assertIn('bind-check-interval=60', dropin)
            self.assertIn('primary=yes', dropin)
            self.assertEqual(commands[-1], ['pdns_server', '--config=check'])

    def test_systemd_path_reloads_new_and_changed_zones(self):
        writes: dict[str, str] = {}
        commands: list[list[str]] = []
        with mock.patch.object(OPERATIONS.shutil, 'which', return_value='/usr/bin/pdns_control'), \
             mock.patch.object(
                 OPERATIONS, 'write_atomic_root',
                 side_effect=lambda path, text, mode=0o644: writes.__setitem__(path.name, text),
             ), \
             mock.patch.object(
                 OPERATIONS, 'run_command',
                 side_effect=lambda command, **kwargs: commands.append(command)
                 or subprocess.CompletedProcess(command, 0, '', ''),
             ):
            OPERATIONS.install_powerdns_units(
                pathlib.Path('/etc/bind/named.conf.local'),
                pathlib.Path('/etc/bind/zones'),
                'bind',
            )
        self.assertIn('SupplementaryGroups=bind', writes['hostpanel.conf'])
        self.assertIn('pdns_control rediscover', writes['hostpanel-pdns-reload.service'])
        self.assertIn('pdns_control reload', writes['hostpanel-pdns-reload.service'])
        self.assertIn(
            'PathChanged=/etc/bind/named.conf.local',
            writes['hostpanel-pdns-zones.path'],
        )
        self.assertIn('PathModified=/etc/bind/zones', writes['hostpanel-pdns-zones.path'])
        self.assertIn(['systemctl', 'daemon-reload'], commands)

    def test_powerdns_switch_stops_bind_only_after_preparation(self):
        options = dict(CONFIG.DEFAULT_OPTIONS)
        options['dns'] = 'powerdns'
        platform = CONFIG.Platform('debian', 'ubuntu', '24.04')
        commands: list[list[str]] = []
        with mock.patch.object(
            OPERATIONS, 'dns_layout',
            return_value=(
                pathlib.Path('/etc/bind/named.conf.local'),
                pathlib.Path('/etc/bind/zones'),
                'bind',
                'bind9.service',
            ),
        ), mock.patch.object(
            OPERATIONS, 'service_active',
            side_effect=lambda name: name == 'bind9.service',
        ), mock.patch.object(
            OPERATIONS, 'unmask_service',
        ), mock.patch.object(
            OPERATIONS, 'persist_dns_mode',
        ) as persist, mock.patch.object(
            OPERATIONS, 'run_command',
            side_effect=lambda command, **kwargs: commands.append(command)
            or subprocess.CompletedProcess(command, 0, '', ''),
        ):
            OPERATIONS.reconcile_dns_services(options, platform, pathlib.Path('/tmp/log'))
        self.assertIn(['systemctl', 'stop', 'bind9.service'], commands)
        self.assertIn(['systemctl', 'enable', '--now', 'pdns.service'], commands)
        self.assertIn(['pdns_control', 'rediscover'], commands)
        self.assertIn(['pdns_control', 'reload'], commands)
        self.assertIn(
            ['systemctl', 'enable', '--now', 'hostpanel-pdns-zones.path'], commands
        )
        self.assertIn(['systemctl', 'disable', 'bind9.service'], commands)
        persist.assert_called_once_with('powerdns')

    def test_failed_powerdns_start_restores_bind(self):
        options = dict(CONFIG.DEFAULT_OPTIONS)
        options['dns'] = 'powerdns'
        platform = CONFIG.Platform('debian', 'ubuntu', '24.04')
        commands: list[list[str]] = []

        def run(command, **kwargs):
            commands.append(command)
            if command == ['systemctl', 'enable', '--now', 'pdns.service']:
                raise CONFIG.BuildError('pdns failed')
            return subprocess.CompletedProcess(command, 0, '', '')

        with mock.patch.object(
            OPERATIONS, 'dns_layout',
            return_value=(
                pathlib.Path('/etc/bind/named.conf.local'),
                pathlib.Path('/etc/bind/zones'),
                'bind',
                'bind9.service',
            ),
        ), mock.patch.object(
            OPERATIONS, 'service_active',
            side_effect=lambda name: name == 'bind9.service',
        ), mock.patch.object(OPERATIONS, 'unmask_service'), \
             mock.patch.object(OPERATIONS, 'run_command', side_effect=run):
            with self.assertRaisesRegex(CONFIG.BuildError, 'pdns failed'):
                OPERATIONS.reconcile_dns_services(
                    options, platform, pathlib.Path('/tmp/log')
                )
        self.assertIn(
            ['systemctl', 'enable', '--now', 'bind9.service'], commands
        )

    def test_powerdns_preflight_is_fail_closed(self):
        options = dict(CONFIG.DEFAULT_OPTIONS)
        options['dns'] = 'powerdns'
        platform = CONFIG.Platform('debian', 'ubuntu', '26.04')
        with mock.patch.object(OPERATIONS, 'candidate_version', return_value=None):
            with self.assertRaisesRegex(CONFIG.BuildError, 'PowerDNS Authoritative'):
                OPERATIONS.preflight_packages(['dns'], options, platform)


if __name__ == '__main__':
    unittest.main()
