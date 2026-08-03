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

    def test_varnish_failure_repairs_direct_origin(self):
        options = dict(CONFIG.DEFAULT_OPTIONS)
        options['varnish'] = 'on'
        repaired: list[tuple[bool, int]] = []
        with mock.patch.object(
            STATE.base, 'apply_varnish',
            side_effect=CONFIG.BuildError('validation failed'),
        ), mock.patch.object(
            STATE.base, 'write_atomic_text',
        ), mock.patch.object(
            STATE.base, 'rewrite_varnish_proxies',
            side_effect=lambda enabled, port, log: repaired.append((enabled, port)),
        ), mock.patch.object(
            STATE, 'run_command',
            return_value=subprocess.CompletedProcess([], 0, '', ''),
        ):
            with self.assertRaisesRegex(CONFIG.BuildError, 'validation failed'):
                STATE.apply_varnish(
                    options, CONFIG.Platform('debian', 'ubuntu', '24.04'),
                    pathlib.Path('/tmp/log'), pathlib.Path('/tmp/backup'),
                )
        self.assertEqual(repaired, [(False, 8080)])

    def test_active_varnish_blocks_webserver_mode_change(self):
        options = dict(CONFIG.DEFAULT_OPTIONS)
        options['webserver'] = 'openlitespeed'
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            current = root / 'webserver-mode'
            current.write_text('nginx_apache\n', encoding='ascii')
            with mock.patch.object(STATE, '_runtime_mode', return_value='on'):
                with self.assertRaisesRegex(CONFIG.BuildError, 'disable active Varnish'):
                    STATE.ensure_safe_web_switch('web', options, current)
            with mock.patch.object(STATE, '_runtime_mode', return_value='off'):
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

    def test_entry_and_installer_include_state_adapters(self):
        entry = (TOOLS / 'hostpanel_build_entry.py').read_text(encoding='utf-8')
        launcher = (TOOLS / 'hostpanel-build.py').read_text(encoding='utf-8')
        installer = (TOOLS / 'install-hostpanel-build.sh').read_text(encoding='utf-8')
        subprocess.run(
            ['bash', '-n', str(TOOLS / 'install-hostpanel-build.sh')], check=True
        )
        self.assertIn('ensure_safe_web_switch', entry)
        self.assertIn('from hostpanel_build_entry import main', launcher)
        self.assertIn('hostpanel_build_extras_state.py', installer)
        self.assertIn('hostpanel_build_entry.py', installer)
        self.assertIn('patch_extras_doctor.py', installer)
        self.assertIn('/etc/hostpanel/mongodb-mode', installer)


if __name__ == '__main__':
    unittest.main()
