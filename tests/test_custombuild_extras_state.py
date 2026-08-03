from __future__ import annotations

import importlib.util
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
import hostpanel_build_extras_state as STATE


class CustomBuildExtrasStateTests(unittest.TestCase):
    def test_mongodb_off_requires_an_inactive_service(self):
        options = dict(CONFIG.DEFAULT_OPTIONS)
        inactive = subprocess.CompletedProcess(['systemctl'], 3, '', '')
        with mock.patch.object(STATE, '_runtime_mode', return_value='off'), \
             mock.patch.object(STATE, 'run_command', return_value=inactive):
            STATE.validate_mongodb(options, pathlib.Path('/tmp/log'))
        active = subprocess.CompletedProcess(['systemctl'], 0, '', '')
        with mock.patch.object(STATE, '_runtime_mode', return_value='off'), \
             mock.patch.object(STATE, 'run_command', return_value=active):
            with self.assertRaisesRegex(CONFIG.BuildError, 'active'):
                STATE.validate_mongodb(options, pathlib.Path('/tmp/log'))

    def test_mongodb_runtime_must_match_build_config(self):
        options = dict(CONFIG.DEFAULT_OPTIONS)
        options['mongodb'] = '8.0'
        with mock.patch.object(STATE, '_runtime_mode', return_value='off'):
            with self.assertRaisesRegex(CONFIG.BuildError, 'does not match'):
                STATE.validate_mongodb(options, pathlib.Path('/tmp/log'))

    def test_mongodb_hardening_is_idempotent_and_fail_closed(self):
        original = 'net:\n  bindIp: localhost,::1\nsecurity:\n  authorization: true\n'
        updated = STATE.harden_mongod_config(original)
        self.assertEqual(updated, STATE.harden_mongod_config(updated))
        self.assertIn('bindIp: 127.0.0.1', updated)
        self.assertIn('authorization: enabled', updated)
        with self.assertRaisesRegex(CONFIG.BuildError, 'bindIpAll'):
            STATE.harden_mongod_config('net:\n  bindIpAll: true\n')
        with self.assertRaisesRegex(CONFIG.BuildError, 'multiple top-level net'):
            STATE.harden_mongod_config('net:\nnet:\n')

    def test_vcl_rejects_all_http_purge_requests(self):
        vcl = STATE.render_varnish_vcl(8080)
        self.assertIn('if (req.method == "PURGE") { return (synth(405)); }', vcl)
        self.assertNotIn('acl purge', vcl)
        self.assertNotIn('return (purge)', vcl)

    def test_missing_secret_does_not_require_preinstall_service_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            with mock.patch.object(
                STATE, 'VARNISH_SECRET_CANDIDATES',
                (root / 'secret', root / 'varnish_secret'),
            ), mock.patch.object(STATE, '_varnish_identity') as identity:
                self.assertIsNone(STATE._existing_varnish_secret())
        identity.assert_not_called()

    def test_varnish_validation_requires_authenticated_management(self):
        options = dict(CONFIG.DEFAULT_OPTIONS)
        options['varnish'] = 'on'
        with mock.patch.object(STATE.base, 'validate_varnish'), \
             mock.patch.object(STATE.base, 'loopback_listener', return_value=False):
            with self.assertRaisesRegex(CONFIG.BuildError, 'management port'):
                STATE.validate_varnish(options, pathlib.Path('/tmp/log'))

        commands: list[list[str]] = []
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            secret = root / 'secret'
            secret.write_text('a' * 48 + '\n', encoding='ascii')
            dropin = root / 'hostpanel.conf'
            dropin.write_text(
                f'[Service]\nExecStart=/usr/sbin/varnishd -S {secret}\n',
                encoding='utf-8',
            )
            with mock.patch.object(STATE.base, 'validate_varnish'), \
                 mock.patch.object(STATE.base, 'loopback_listener', return_value=True), \
                 mock.patch.object(STATE, '_existing_varnish_secret', return_value=secret), \
                 mock.patch.object(STATE, '_trusted_config'), \
                 mock.patch.object(STATE.base, 'VARNISH_DROPIN', dropin), \
                 mock.patch.object(STATE.shutil, 'which', return_value='/usr/bin/varnishadm'), \
                 mock.patch.object(
                     STATE, 'run_command',
                     side_effect=lambda command, **kwargs: commands.append(command)
                     or subprocess.CompletedProcess(command, 0, '', ''),
                 ):
                STATE.validate_varnish(options, pathlib.Path('/tmp/log'))
        self.assertEqual(
            commands,
            [[
                '/usr/bin/varnishadm', '-T', '127.0.0.1:6082',
                '-S', str(secret), 'ping',
            ]],
        )

    def test_active_varnish_blocks_webserver_mode_change(self):
        options = dict(CONFIG.DEFAULT_OPTIONS)
        options['webserver'] = 'openlitespeed'
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            current = root / 'webserver-mode'
            current.write_text('nginx_apache\n', encoding='ascii')
            with mock.patch.object(
                STATE, '_runtime_mode', side_effect=['nginx_apache', 'on']
            ):
                with self.assertRaisesRegex(CONFIG.BuildError, 'disable active Varnish'):
                    STATE.ensure_safe_web_switch('web', options, current)
            with mock.patch.object(
                STATE, '_runtime_mode', side_effect=['nginx_apache', 'off']
            ):
                STATE.ensure_safe_web_switch('web', options, current)

    def test_extras_doctor_patcher_is_idempotent(self):
        spec = importlib.util.spec_from_file_location(
            'patch_extras_doctor', TOOLS / 'patch_extras_doctor.py'
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        source = 'from pathlib import Path\n\n' + module.DNS_BLOCK
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / 'hostpanel-doctor'
            path.write_text(source, encoding='utf-8')
            path.chmod(0o755)
            with mock.patch.object(module, 'trusted_file', side_effect=lambda item: item.stat()):
                module.patch(path)
                first = path.read_text(encoding='utf-8')
                module.patch(path)
            self.assertEqual(first, path.read_text(encoding='utf-8'))
            self.assertIn('/etc/hostpanel/mongodb-mode', first)
            self.assertIn('/etc/hostpanel/varnish-mode', first)
            self.assertIn('expected["mongod"]', first)
            self.assertIn('expected["varnish"]', first)

    def test_entry_and_installer_include_all_state_adapters(self):
        entry = (TOOLS / 'hostpanel_build_entry.py').read_text(encoding='utf-8')
        launcher = (TOOLS / 'hostpanel-build.py').read_text(encoding='utf-8')
        installer = (TOOLS / 'install-hostpanel-build.sh').read_text(encoding='utf-8')
        subprocess.run(
            ['bash', '-n', str(TOOLS / 'install-hostpanel-build.sh')], check=True
        )
        self.assertIn('ensure_safe_web_switch', entry)
        self.assertIn('state.install()', entry)
        self.assertIn('cli.validate_varnish = state.validate_varnish', entry)
        self.assertIn('from hostpanel_build_entry import main', launcher)
        self.assertIn('hostpanel_build_extras_state.py', installer)
        self.assertIn('hostpanel_build_entry.py', installer)
        self.assertIn('hostpanel_build_mongodb_adapter.py', installer)
        self.assertIn('patch_extras_doctor.py', installer)
        self.assertIn('/etc/hostpanel/mongodb-mode', installer)


if __name__ == '__main__':
    unittest.main()
