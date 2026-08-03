from __future__ import annotations

import contextlib
import os
import pathlib
import re
import shutil
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
import hostpanel_build_entry as ENTRY
import hostpanel_build_extras_state as STATE
import hostpanel_build_mongodb_adapter as MONGODB_ADAPTER
import hostpanel_build_powerdns_adapter as POWERDNS


class DeepCustomBuildRegressionTests(unittest.TestCase):
    def test_installed_layout_imports_every_runtime_module(self):
        installer = (TOOLS / 'install-hostpanel-build.sh').read_text(encoding='utf-8')
        match = re.search(r'^MODULES=\(\n(?P<body>.*?)^\)$', installer, re.M | re.S)
        self.assertIsNotNone(match)
        modules = [line.strip() for line in match.group('body').splitlines() if line.strip()]
        self.assertIn('hostpanel_build_mongodb_adapter.py', modules)
        with tempfile.TemporaryDirectory() as directory:
            installed = pathlib.Path(directory)
            launcher = installed / 'hostpanel-build'
            shutil.copy2(TOOLS / 'hostpanel-build.py', launcher)
            launcher.chmod(0o755)
            for module in modules:
                shutil.copy2(TOOLS / module, installed / module)
            config = installed / 'build.conf'
            config.write_text(CONFIG.render_config(CONFIG.DEFAULT_OPTIONS), encoding='utf-8')
            config.chmod(0o600)
            environment = dict(os.environ)
            environment.pop('PYTHONPATH', None)
            completed = subprocess.run(
                [
                    sys.executable, str(launcher), '--allow-non-root',
                    '--config', str(config), 'options',
                ],
                cwd=installed, env=environment, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                check=False,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn('dns=bind', completed.stdout)
        self.assertIn('mongodb=off', completed.stdout)
        self.assertIn('varnish=off', completed.stdout)

    def test_installer_preserves_applied_modes_and_rejects_links(self):
        source = (TOOLS / 'install-hostpanel-build.sh').read_text(encoding='utf-8')
        self.assertIn('hostpanel_build_mongodb_adapter.py', source)
        self.assertIn('ensure_mode_file "$MODE_FILE" nginx_apache', source)
        self.assertIn('ensure_mode_file "$DNS_MODE_FILE" bind', source)
        self.assertIn('ensure_mode_file "$MONGODB_MODE_FILE" off', source)
        self.assertIn('ensure_mode_file "$VARNISH_MODE_FILE" off', source)
        self.assertIn('stat -c %u:%g:%h', source)
        self.assertNotIn('printf \'%s\\n\' "$WEB_MODE" >"$MODE_FILE"', source)
        subprocess.run(['bash', '-n', str(TOOLS / 'install-hostpanel-build.sh')], check=True)

    def test_workflow_tracks_and_compiles_all_adapters(self):
        workflow = (ROOT / '.github/workflows/automatic-installer.yml').read_text(
            encoding='utf-8'
        )
        self.assertGreaterEqual(workflow.count('tools/hostpanel_build_mongodb_adapter.py'), 3)
        self.assertIn('tests/test_custombuild_deep_regressions.py', workflow)
        self.assertIn('tests.test_custombuild_deep_regressions', workflow)
        self.assertIn('python3 -m compileall -q tools tests', workflow)

    def test_mongod_hardening_rejects_ambiguous_or_exposed_yaml(self):
        good = (
            'storage:\n  dbPath: /var/lib/mongodb\n'
            'net:\n  bindIp: 127.0.0.1 # local only\n'
            'security:\n  authorization: true\n'
        )
        hardened = STATE.harden_mongod_config(good)
        self.assertIn('bindIp: 127.0.0.1', hardened)
        self.assertIn('authorization: enabled', hardened)
        self.assertEqual(STATE.harden_mongod_config(hardened), hardened)
        rejected = (
            'net:\n  bindIp: 127.0.0.1\nnet:\n  bindIp: 127.0.0.1\n',
            'net: { bindIp: 127.0.0.1 }\n',
            'net:\n  bindIpAll: true\n',
            'net:\n  bindIp: 0.0.0.0\n',
            'security:\n  authorization: disabled\n',
            'security:\n  authorization: enabled\n  authorization: true\n',
        )
        for value in rejected:
            with self.subTest(value=value):
                with self.assertRaises(CONFIG.BuildError):
                    STATE.harden_mongod_config(value)

    def test_mongodb_validation_does_not_require_anonymous_database_access(self):
        source = (TOOLS / 'hostpanel_build_extras_state.py').read_text(encoding='utf-8')
        self.assertIn("'--nodb'", source)
        self.assertNotIn('db.adminCommand({ping:1})', source)

    def test_mongodb_masks_service_before_package_scripts(self):
        options = dict(CONFIG.DEFAULT_OPTIONS)
        options['mongodb'] = '8.0'
        events: list[str] = []
        fake_stat = mock.Mock(
            st_mode=stat.S_IFREG | 0o644, st_uid=0, st_gid=0, st_nlink=1
        )
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            config = root / 'mongod.conf'
            config.write_text('net:\n  bindIp: 127.0.0.1\n', encoding='utf-8')
            mode = root / 'mongodb-mode'
            mode.write_text('off\n', encoding='ascii')
            with contextlib.ExitStack() as stack:
                stack.enter_context(mock.patch.object(STATE, 'MONGODB_MODE_FILE', mode))
                stack.enter_context(mock.patch.object(STATE.base, 'MONGODB_CONFIG', config))
                stack.enter_context(mock.patch.object(STATE.base, 'require_root'))
                stack.enter_context(mock.patch.object(STATE.base, 'mongodb_supported'))
                stack.enter_context(mock.patch.object(STATE.base, 'snapshot_paths', return_value=None))
                stack.enter_context(mock.patch.object(STATE.base, 'configure_mongodb_repository'))
                stack.enter_context(mock.patch.object(STATE.base, 'refresh_packages'))
                stack.enter_context(mock.patch.object(STATE.base, 'candidate_version', return_value='8.0'))
                stack.enter_context(mock.patch.object(STATE, '_capture', return_value=None))
                stack.enter_context(mock.patch.object(STATE, '_trusted_config', return_value=fake_stat))
                stack.enter_context(mock.patch.object(
                    STATE, '_mask_service',
                    side_effect=lambda unit, log: events.append('mask') or False,
                ))
                stack.enter_context(mock.patch.object(
                    STATE, '_unmask_service',
                    side_effect=lambda unit, log: events.append('unmask'),
                ))
                stack.enter_context(mock.patch.object(
                    STATE.base, 'reinstall_packages',
                    side_effect=lambda *args: events.append('reinstall'),
                ))
                stack.enter_context(mock.patch.object(
                    STATE.base, 'write_atomic_text',
                    side_effect=lambda path, text, *args: path.write_text(text),
                ))
                stack.enter_context(mock.patch.object(STATE, 'validate_mongodb'))
                stack.enter_context(mock.patch.object(
                    STATE, 'run_command',
                    side_effect=lambda command, **kwargs: events.append(
                        'enable' if command[:3] == ['systemctl', 'enable', '--now'] else 'command'
                    ) or subprocess.CompletedProcess(command, 0, '', ''),
                ))
                STATE.apply_mongodb(
                    options, CONFIG.Platform('debian', 'ubuntu', '24.04'),
                    root / 'log', root / 'backup',
                )
        self.assertLess(events.index('mask'), events.index('reinstall'))
        self.assertLess(events.index('reinstall'), events.index('enable'))
        self.assertIn('unmask', events[:events.index('enable')])

    def test_varnish_masks_service_and_checks_both_loopback_ports(self):
        options = dict(CONFIG.DEFAULT_OPTIONS)
        options['varnish'] = 'on'
        events: list[str] = []
        listeners: list[int] = []
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            mode = root / 'varnish-mode'
            mode.write_text('off\n', encoding='ascii')
            web_mode = root / 'webserver-mode'
            web_mode.write_text('nginx_apache\n', encoding='ascii')
            with contextlib.ExitStack() as stack:
                stack.enter_context(mock.patch.object(STATE, 'VARNISH_MODE_FILE', mode))
                stack.enter_context(mock.patch.object(STATE, 'WEB_MODE_FILE', web_mode))
                stack.enter_context(mock.patch.object(STATE.base, 'VARNISH_VCL', root / 'default.vcl'))
                stack.enter_context(mock.patch.object(STATE.base, 'VARNISH_DROPIN', root / 'hostpanel.conf'))
                stack.enter_context(mock.patch.object(STATE.base, 'NGINX_AVAILABLE', root))
                stack.enter_context(mock.patch.object(STATE.base, 'require_root'))
                stack.enter_context(mock.patch.object(STATE.base, 'snapshot_paths', return_value=None))
                stack.enter_context(mock.patch.object(STATE, '_capture', return_value=None))
                stack.enter_context(mock.patch.object(STATE, '_service_active', return_value=False))
                stack.enter_context(mock.patch.object(
                    STATE, '_mask_service',
                    side_effect=lambda unit, log: events.append('mask') or False,
                ))
                stack.enter_context(mock.patch.object(
                    STATE, '_unmask_service',
                    side_effect=lambda unit, log: events.append('unmask'),
                ))
                stack.enter_context(mock.patch.object(STATE.base, 'refresh_packages'))
                stack.enter_context(mock.patch.object(STATE.base, 'candidate_version', return_value='7.5'))
                stack.enter_context(mock.patch.object(
                    STATE.base, 'reinstall_packages',
                    side_effect=lambda *args: events.append('reinstall'),
                ))
                stack.enter_context(mock.patch.object(STATE.shutil, 'which', return_value='/usr/sbin/varnishd'))
                stack.enter_context(mock.patch.object(
                    STATE.base, 'write_atomic_text',
                    side_effect=lambda path, text, *args: events.append(
                        'mode-on' if path == mode and text == 'on\n' else 'write'
                    ),
                ))
                stack.enter_context(mock.patch.object(STATE.base, 'render_varnish_vcl', return_value='vcl'))
                stack.enter_context(mock.patch.object(STATE.base, 'render_varnish_dropin', return_value='unit'))
                stack.enter_context(mock.patch.object(
                    STATE.base, 'loopback_listener',
                    side_effect=lambda port: listeners.append(port) or True,
                ))
                stack.enter_context(mock.patch.object(
                    STATE.base, 'rewrite_varnish_proxies',
                    side_effect=lambda *args: events.append('rewrite'),
                ))
                stack.enter_context(mock.patch.object(STATE, 'validate_varnish'))
                stack.enter_context(mock.patch.object(
                    STATE, 'run_command',
                    side_effect=lambda command, **kwargs: events.append(
                        'enable' if command[:3] == ['systemctl', 'enable', '--now'] else 'command'
                    ) or subprocess.CompletedProcess(command, 0, '', ''),
                ))
                STATE.apply_varnish(
                    options, CONFIG.Platform('debian', 'ubuntu', '24.04'),
                    root / 'log', root / 'backup',
                )
        self.assertLess(events.index('mask'), events.index('reinstall'))
        self.assertLess(events.index('reinstall'), events.index('enable'))
        self.assertEqual(listeners, [6081, 6082])
        self.assertLess(events.index('mode-on'), events.index('rewrite'))

    def test_varnish_state_write_failure_stops_service(self):
        options = dict(CONFIG.DEFAULT_OPTIONS)
        options['varnish'] = 'on'
        commands: list[list[str]] = []
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            mode = root / 'varnish-mode'
            mode.write_text('off\n')
            web_mode = root / 'webserver-mode'
            web_mode.write_text('nginx_apache\n')
            with contextlib.ExitStack() as stack:
                stack.enter_context(mock.patch.object(STATE, 'VARNISH_MODE_FILE', mode))
                stack.enter_context(mock.patch.object(STATE, 'WEB_MODE_FILE', web_mode))
                stack.enter_context(mock.patch.object(STATE.base, 'VARNISH_VCL', root / 'default.vcl'))
                stack.enter_context(mock.patch.object(STATE.base, 'VARNISH_DROPIN', root / 'dropin'))
                stack.enter_context(mock.patch.object(STATE.base, 'NGINX_AVAILABLE', root))
                stack.enter_context(mock.patch.object(STATE.base, 'require_root'))
                stack.enter_context(mock.patch.object(STATE.base, 'snapshot_paths', return_value=None))
                stack.enter_context(mock.patch.object(STATE, '_capture', return_value=None))
                stack.enter_context(mock.patch.object(STATE, '_service_active', return_value=False))
                stack.enter_context(mock.patch.object(STATE, '_mask_service', return_value=False))
                stack.enter_context(mock.patch.object(STATE, '_unmask_service'))
                stack.enter_context(mock.patch.object(STATE.base, 'refresh_packages'))
                stack.enter_context(mock.patch.object(STATE.base, 'candidate_version', return_value='1'))
                stack.enter_context(mock.patch.object(STATE.base, 'reinstall_packages'))
                stack.enter_context(mock.patch.object(STATE.shutil, 'which', return_value='/usr/sbin/varnishd'))
                stack.enter_context(mock.patch.object(STATE.base, 'render_varnish_vcl', return_value='vcl'))
                stack.enter_context(mock.patch.object(STATE.base, 'render_varnish_dropin', return_value='unit'))
                stack.enter_context(mock.patch.object(STATE.base, 'loopback_listener', return_value=True))
                stack.enter_context(mock.patch.object(STATE.base, 'rewrite_varnish_proxies'))
                stack.enter_context(mock.patch.object(STATE, '_restore'))
                stack.enter_context(mock.patch.object(
                    STATE.base, 'write_atomic_text',
                    side_effect=lambda path, text, *args: (
                        (_ for _ in ()).throw(OSError('state write failed'))
                        if path == mode and text == 'on\n' else None
                    ),
                ))
                stack.enter_context(mock.patch.object(
                    STATE, 'run_command',
                    side_effect=lambda command, **kwargs: commands.append(command)
                    or subprocess.CompletedProcess(command, 0, '', ''),
                ))
                with self.assertRaisesRegex(OSError, 'state write failed'):
                    STATE.apply_varnish(
                        options, CONFIG.Platform('debian', 'ubuntu', '24.04'),
                        root / 'log', root / 'backup',
                    )
        self.assertIn(['systemctl', 'disable', '--now', 'varnish.service'], commands)

    def test_dns_build_failure_rolls_back_previous_mode(self):
        rollback = mock.Mock()
        with mock.patch.object(POWERDNS, 'applied_dns_mode', side_effect=['bind', 'powerdns']), \
             mock.patch.object(POWERDNS.operations, 'service_active', return_value=False), \
             mock.patch.object(POWERDNS.operations, 'reconcile_dns_services', rollback):
            with self.assertRaisesRegex(OSError, 'doctor failed'):
                POWERDNS.guarded_apply_build(
                    mock.Mock(side_effect=OSError('doctor failed')),
                    'dns', {'dns': 'powerdns'}, mock.Mock(), pathlib.Path('/tmp/log'),
                    pathlib.Path('/tmp/backup'), pathlib.Path('/tmp/python'),
                    pathlib.Path('/tmp/doctor'), {'dns'}, pathlib.Path('/tmp/web'),
                    pathlib.Path('/tmp/mode'),
                )
        self.assertEqual(rollback.call_args.args[0]['dns'], 'bind')

    def test_dns_handoff_rolls_back_on_non_build_error(self):
        commands: list[list[str]] = []

        def command(argv, **kwargs):
            commands.append(argv)
            if argv == ['systemctl', 'enable', '--now', 'pdns.service']:
                raise OSError('systemd I/O failed')
            return subprocess.CompletedProcess(argv, 0, '', '')

        with mock.patch.object(POWERDNS.operations, 'dns_layout', return_value=(
            pathlib.Path('/bind'), pathlib.Path('/zones'), 'bind', 'bind9.service'
        )), mock.patch.object(
            POWERDNS.operations, 'service_active', side_effect=[True, False]
        ), mock.patch.object(POWERDNS.operations, 'unmask_service'), \
             mock.patch.object(POWERDNS, 'run_command', side_effect=command):
            with self.assertRaisesRegex(OSError, 'systemd I/O failed'):
                POWERDNS.reconcile_dns_services(
                    {'dns': 'powerdns'}, mock.Mock(), pathlib.Path('/tmp/log')
                )
        self.assertIn(['systemctl', 'enable', '--now', 'bind9.service'], commands)

    def test_entry_adapter_wrapping_is_idempotent(self):
        with mock.patch.object(ENTRY.powerdns_adapter, 'install'), \
             mock.patch.object(ENTRY.mongodb_adapter, 'install'), \
             mock.patch.object(ENTRY.state, 'install'), \
             mock.patch.object(ENTRY.state, 'ensure_safe_web_switch'), \
             mock.patch.object(
                 ENTRY.powerdns_adapter, 'guarded_apply_build', return_value='ok'
             ) as guarded:
            ENTRY.install_runtime_adapters(pathlib.Path('/tmp/one'))
            ENTRY.install_runtime_adapters(pathlib.Path('/tmp/two'))
            result = ENTRY.cli.apply_build(
                'dns', {'dns': 'bind'}, mock.Mock(), pathlib.Path('/tmp/log'),
                pathlib.Path('/tmp/backup'), pathlib.Path('/tmp/python'),
                pathlib.Path('/tmp/doctor'), {'dns'}, pathlib.Path('/tmp/web'),
                pathlib.Path('/tmp/mode'),
            )
        self.assertEqual(result, 'ok')
        self.assertIs(guarded.call_args.args[0], ENTRY._BASE_APPLY_BUILD)

    def test_mongodb_runtime_directory_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            real = root / 'real'
            real.mkdir()
            link = root / 'runtime'
            link.symlink_to(real, target_is_directory=True)
            with self.assertRaisesRegex(CONFIG.BuildError, 'unsafe MongoDB runtime'):
                MONGODB_ADAPTER.ensure_private_runtime_dir(link)

    def test_powerdns_parent_chain_is_validated_before_creation(self):
        target = pathlib.Path('/safe/pdns.d')
        calls: list[pathlib.Path] = []
        with mock.patch.object(POWERDNS.os.path, 'lexists', side_effect=lambda path: path == pathlib.Path('/safe')), \
             mock.patch.object(
                 POWERDNS, 'trusted_root_directory_chain',
                 side_effect=lambda path: calls.append(path),
             ), mock.patch.object(pathlib.Path, 'mkdir'):
            POWERDNS.prepare_include_directory(target)
        self.assertEqual(calls, [pathlib.Path('/safe'), target])

    def test_production_validator_checks_dns_udp_tcp_and_queries(self):
        source = (TOOLS / 'validate-production-vm.sh').read_text(encoding='utf-8')
        self.assertIn('ss -lnuH', source)
        self.assertIn('authoritative DNS UDP port 53 is listening', source)
        self.assertIn("dig @127.0.0.1 . SOA", source)
        self.assertIn('+tcp +time=2', source)
        subprocess.run(['bash', '-n', str(TOOLS / 'validate-production-vm.sh')], check=True)

    def test_all_python_sources_compile(self):
        subprocess.run(
            [sys.executable, '-m', 'compileall', '-q', 'tools', 'tests'],
            cwd=ROOT, check=True,
        )


if __name__ == '__main__':
    unittest.main()
