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

import hostpanel_build_cli as CLI
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
        assert match is not None
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

    def test_installer_preserves_applied_modes_and_rejects_dangling_links(self):
        source = (TOOLS / 'install-hostpanel-build.sh').read_text(encoding='utf-8')
        self.assertIn('hostpanel_build_mongodb_adapter.py', source)
        self.assertIn('if [[ ! -e "$CONFIG" && ! -L "$CONFIG" ]]; then', source)
        self.assertIn('if [[ ! -e "$path" && ! -L "$path" ]]; then', source)
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
        self.assertIn('tests/test_custombuild_platform_guards.py', workflow)
        self.assertIn('tests.test_custombuild_deep_regressions', workflow)
        self.assertIn('tests.test_custombuild_platform_guards', workflow)
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
            with self.subTest(value=value), self.assertRaises(CONFIG.BuildError):
                STATE.harden_mongod_config(value)

    def test_runtime_mode_rejects_symlink_and_hardlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            real = root / 'real-mode'
            real.write_text('off\n', encoding='ascii')
            link = root / 'mode-link'
            link.symlink_to(real)
            with self.assertRaisesRegex(CONFIG.BuildError, 'unsafe runtime mode'):
                STATE._runtime_mode(link, 'off')
            hard = root / 'mode-hard'
            os.link(real, hard)
            with self.assertRaisesRegex(CONFIG.BuildError, 'unsafe runtime mode'):
                STATE._runtime_mode(real, 'off')

    def test_mongodb_validation_connects_to_server_without_anonymous_ping(self):
        options = dict(CONFIG.DEFAULT_OPTIONS)
        options['mongodb'] = '8.0'
        commands: list[list[str]] = []
        with tempfile.TemporaryDirectory() as directory:
            config = pathlib.Path(directory) / 'mongod.conf'
            config.write_text(
                'net:\n  bindIp: 127.0.0.1\n'
                'security:\n  authorization: enabled\n',
                encoding='utf-8',
            )
            with mock.patch.object(STATE, 'MONGODB_MODE_FILE', pathlib.Path('/mode')), \
                 mock.patch.object(STATE, '_runtime_mode', return_value='8.0'), \
                 mock.patch.object(STATE.base, 'MONGODB_CONFIG', config), \
                 mock.patch.object(STATE, '_trusted_config'), \
                 mock.patch.object(STATE.shutil, 'which', return_value='/usr/bin/tool'), \
                 mock.patch.object(STATE.base, 'loopback_listener', return_value=True), \
                 mock.patch.object(
                     STATE, 'run_command',
                     side_effect=lambda command, **kwargs: commands.append(command)
                     or subprocess.CompletedProcess(
                         command, 0,
                         'enabled\n' if command[:2] == ['systemctl', 'is-enabled'] else '',
                         '',
                     ),
                 ):
                STATE.validate_mongodb(options, pathlib.Path('/tmp/log'))
        mongosh = next(command for command in commands if command[0] == 'mongosh')
        self.assertIn('--host', mongosh)
        self.assertIn('127.0.0.1', mongosh)
        self.assertNotIn('--nodb', mongosh)
        self.assertNotIn('db.adminCommand({ping:1})', ' '.join(mongosh))

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

    def test_varnish_masks_service_checks_ports_and_repairs_proxies(self):
        options = dict(CONFIG.DEFAULT_OPTIONS)
        options['varnish'] = 'on'
        events: list[str] = []
        listeners: list[int] = []
        proxy_states: list[bool] = []
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            mode = root / 'varnish-mode'
            mode.write_text('off\n', encoding='ascii')
            web_mode = root / 'webserver-mode'
            web_mode.write_text('nginx_apache\n', encoding='ascii')
            secret = root / 'secret'
            with contextlib.ExitStack() as stack:
                stack.enter_context(mock.patch.object(STATE, 'VARNISH_MODE_FILE', mode))
                stack.enter_context(mock.patch.object(STATE, 'WEB_MODE_FILE', web_mode))
                stack.enter_context(mock.patch.object(
                    STATE, 'VARNISH_SECRET_CANDIDATES',
                    (root / 'secret', root / 'varnish_secret'),
                ))
                stack.enter_context(mock.patch.object(STATE.base, 'VARNISH_VCL', root / 'default.vcl'))
                stack.enter_context(mock.patch.object(STATE.base, 'VARNISH_DROPIN', root / 'hostpanel.conf'))
                stack.enter_context(mock.patch.object(STATE.base, 'NGINX_AVAILABLE', root))
                stack.enter_context(mock.patch.object(STATE.base, 'require_root'))
                stack.enter_context(mock.patch.object(STATE.base, 'snapshot_paths', return_value=None))
                stack.enter_context(mock.patch.object(STATE, '_capture', return_value=None))
                stack.enter_context(mock.patch.object(STATE, '_existing_varnish_secret', return_value=None))
                stack.enter_context(mock.patch.object(STATE, '_ensure_varnish_secret', return_value=secret))
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
                    side_effect=lambda enabled, *args: proxy_states.append(enabled),
                ))
                stack.enter_context(mock.patch.object(
                    STATE, 'validate_varnish',
                    side_effect=CONFIG.BuildError('final validation failed'),
                ))
                stack.enter_context(mock.patch.object(
                    STATE, 'run_command',
                    side_effect=lambda command, **kwargs: events.append(
                        'enable' if command[:3] == ['systemctl', 'enable', '--now'] else 'command'
                    ) or subprocess.CompletedProcess(command, 0, '', ''),
                ))
                with self.assertRaisesRegex(CONFIG.BuildError, 'final validation failed'):
                    STATE.apply_varnish(
                        options, CONFIG.Platform('debian', 'ubuntu', '24.04'),
                        root / 'log', root / 'backup',
                    )
        self.assertLess(events.index('mask'), events.index('reinstall'))
        self.assertLess(events.index('reinstall'), events.index('enable'))
        self.assertEqual(listeners, [6081, 6082])
        self.assertEqual(proxy_states, [True, False])

    def test_varnish_secret_generation_is_private_and_group_readable(self):
        identity = mock.Mock(pw_gid=456)
        target = pathlib.Path('/etc/varnish/secret')
        with mock.patch.object(STATE, 'VARNISH_SECRET_CANDIDATES', (
            target, pathlib.Path('/etc/varnish/varnish_secret')
        )), mock.patch.object(STATE, '_varnish_identity', return_value=identity), \
             mock.patch.object(STATE, '_existing_varnish_secret', return_value=None), \
             mock.patch.object(pathlib.Path, 'mkdir'), \
             mock.patch.object(STATE, '_trusted_root_directory_chain'), \
             mock.patch.object(STATE.secrets, 'token_urlsafe', return_value='x' * 64), \
             mock.patch.object(STATE.base, 'write_atomic_bytes') as write, \
             mock.patch.object(
                 STATE, '_trusted_varnish_secret', return_value=target
             ) as trusted:
            self.assertEqual(STATE._ensure_varnish_secret(), target)
        write.assert_called_once_with(target, (('x' * 64) + '\n').encode('ascii'), 0o640, 0, 456)
        trusted.assert_called_once_with(target, 456)

    def test_varnish_secret_rejects_symlink_and_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            secret = root / 'secret'
            secret.write_text('x' * 48 + '\n', encoding='ascii')
            link = root / 'secret-link'
            link.symlink_to(secret)
            with self.assertRaisesRegex(CONFIG.BuildError, 'unsafe Varnish secret'):
                STATE._trusted_varnish_secret(link, 123)
            first = root / 'first'
            second = root / 'second'
            first.write_text('x' * 48 + '\n')
            second.write_text('y' * 48 + '\n')
            with mock.patch.object(STATE, 'VARNISH_SECRET_CANDIDATES', (first, second)), \
                 mock.patch.object(STATE, '_varnish_identity', return_value=mock.Mock(pw_gid=123)):
                with self.assertRaisesRegex(CONFIG.BuildError, 'multiple Varnish secret'):
                    STATE._existing_varnish_secret()

    def test_execute_build_runs_final_doctor_only_when_needed(self):
        options = dict(CONFIG.DEFAULT_OPTIONS)
        common = (
            CONFIG.Platform('debian', 'ubuntu', '24.04'),
            pathlib.Path('/tmp/log'), pathlib.Path('/tmp/backup'),
            pathlib.Path('/tmp/python'), pathlib.Path('/tmp/doctor'),
        )
        with mock.patch.object(CLI, 'apply_build') as core, \
             mock.patch.object(CLI, 'apply_mongodb') as mongodb, \
             mock.patch.object(CLI, 'apply_varnish') as varnish, \
             mock.patch.object(CLI, 'run_doctor') as doctor:
            CLI.execute_build(
                'dns', options, *common, {'dns'},
                pathlib.Path('/tmp/web'), pathlib.Path('/tmp/mode'),
            )
            doctor.assert_not_called()
            CLI.execute_build(
                'mongodb', {**options, 'mongodb': '8.0'}, *common, {'database'},
                pathlib.Path('/tmp/web'), pathlib.Path('/tmp/mode'),
            )
            doctor.assert_called_once()
            doctor.reset_mock()
            CLI.execute_build(
                'all', {**options, 'mongodb': '8.0'}, *common,
                {'web', 'database', 'dns'},
                pathlib.Path('/tmp/web'), pathlib.Path('/tmp/mode'),
            )
            doctor.assert_called_once()
        self.assertGreaterEqual(core.call_count, 2)
        self.assertGreaterEqual(mongodb.call_count, 2)
        self.assertGreaterEqual(varnish.call_count, 1)

    def test_dns_failure_after_optional_components_rolls_back_previous_mode(self):
        rollback = mock.Mock()
        with mock.patch.object(POWERDNS, 'applied_dns_mode', side_effect=['bind', 'powerdns']), \
             mock.patch.object(POWERDNS.operations, 'service_active', return_value=False), \
             mock.patch.object(POWERDNS.operations, 'reconcile_dns_services', rollback):
            with self.assertRaisesRegex(OSError, 'final doctor failed'):
                POWERDNS.guarded_apply_build(
                    mock.Mock(side_effect=OSError('final doctor failed')),
                    'all', {'dns': 'powerdns'}, mock.Mock(), pathlib.Path('/tmp/log'),
                    pathlib.Path('/tmp/backup'), pathlib.Path('/tmp/python'),
                    pathlib.Path('/tmp/doctor'), {'dns', 'web'},
                    pathlib.Path('/tmp/web'), pathlib.Path('/tmp/mode'),
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

    def test_entry_wraps_complete_transaction_idempotently(self):
        original_build = ENTRY.cli.execute_build
        original_plan = ENTRY.cli.print_plan
        self.addCleanup(setattr, ENTRY.cli, 'execute_build', original_build)
        self.addCleanup(setattr, ENTRY.cli, 'print_plan', original_plan)
        with mock.patch.object(ENTRY.powerdns_adapter, 'install'), \
             mock.patch.object(ENTRY.mongodb_adapter, 'install'), \
             mock.patch.object(ENTRY.state, 'install'), \
             mock.patch.object(ENTRY.state, 'ensure_safe_web_switch'), \
             mock.patch.object(
                 ENTRY.powerdns_adapter, 'guarded_apply_build', return_value='ok'
             ) as guarded:
            ENTRY.install_runtime_adapters(pathlib.Path('/tmp/one'))
            ENTRY.install_runtime_adapters(pathlib.Path('/tmp/two'))
            result = ENTRY.cli.execute_build(
                'dns', {'dns': 'bind'}, mock.Mock(), pathlib.Path('/tmp/log'),
                pathlib.Path('/tmp/backup'), pathlib.Path('/tmp/python'),
                pathlib.Path('/tmp/doctor'), {'dns'}, pathlib.Path('/tmp/web'),
                pathlib.Path('/tmp/mode'),
            )
        self.assertEqual(result, 'ok')
        self.assertIs(guarded.call_args.args[0], ENTRY._BASE_EXECUTE_BUILD)

    def test_plan_rejects_unsupported_mongodb_target(self):
        original_plan = ENTRY.cli.print_plan
        self.addCleanup(setattr, ENTRY.cli, 'print_plan', original_plan)
        with mock.patch.object(ENTRY.powerdns_adapter, 'install'), \
             mock.patch.object(ENTRY.mongodb_adapter, 'install'), \
             mock.patch.object(ENTRY.state, 'install'), \
             mock.patch.object(
                 ENTRY.mongodb_adapter, 'mongodb_supported',
                 side_effect=CONFIG.BuildError('unsupported target'),
             ):
            ENTRY.install_runtime_adapters(pathlib.Path('/tmp/config'))
            with self.assertRaisesRegex(CONFIG.BuildError, 'unsupported target'):
                ENTRY.cli.print_plan(
                    'mongodb', {**CONFIG.DEFAULT_OPTIONS, 'mongodb': '8.0'},
                    CONFIG.Platform('debian', 'ubuntu', '26.04'), None,
                )

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
        with mock.patch.object(
            POWERDNS.os.path, 'lexists',
            side_effect=lambda path: path == pathlib.Path('/safe'),
        ), mock.patch.object(
            POWERDNS, 'trusted_root_directory_chain',
            side_effect=lambda path: calls.append(path),
        ), mock.patch.object(pathlib.Path, 'mkdir'):
            POWERDNS.prepare_include_directory(target)
        self.assertEqual(calls, [pathlib.Path('/safe'), target])

    def test_production_validator_checks_dns_udp_tcp_and_queries(self):
        source = (TOOLS / 'validate-production-vm.sh').read_text(encoding='utf-8')
        self.assertIn('ss -lnuH', source)
        self.assertIn('authoritative DNS UDP port 53 is listening', source)
        self.assertIn('dig @127.0.0.1 . SOA', source)
        self.assertIn('+tcp +time=2', source)
        subprocess.run(['bash', '-n', str(TOOLS / 'validate-production-vm.sh')], check=True)

    def test_all_python_sources_compile(self):
        subprocess.run(
            [sys.executable, '-m', 'compileall', '-q', 'tools', 'tests'],
            cwd=ROOT, check=True,
        )


if __name__ == '__main__':
    unittest.main()
