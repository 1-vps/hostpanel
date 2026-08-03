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
import hostpanel_build_powerdns_adapter as ADAPTER


class PowerDnsRuntimeTests(unittest.TestCase):
    def test_parser_accepts_whitespace_and_append_forms(self):
        text = '''
include-dir = /etc/powerdns/pdns.d
launch = bind
launch += gsqlite3
'''
        self.assertEqual(
            ADAPTER.setting_pairs(text),
            [
                ('include-dir', False, '/etc/powerdns/pdns.d'),
                ('launch', False, 'bind'),
                ('launch', True, 'gsqlite3'),
            ],
        )
        self.assertEqual(ADAPTER.launch_values(text), {'bind', 'gsqlite3'})
        self.assertEqual(
            ADAPTER.active_setting_keys(text), {'include-dir', 'launch'}
        )

    def test_whitespace_unmanaged_backend_is_rejected_by_existing_selector(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            native = root / 'pdns.conf'
            include = root / 'pdns.d'
            include.mkdir()
            native.write_text(
                f'include-dir = {include}\nlaunch = gsqlite3\n', encoding='utf-8'
            )
            with mock.patch.object(
                ADAPTER.operations, 'powerdns_include_dir', ADAPTER.powerdns_include_dir
            ), mock.patch.object(
                ADAPTER.operations, 'launch_values', ADAPTER.launch_values
            ), mock.patch.object(
                ADAPTER.operations, 'active_setting_keys', ADAPTER.active_setting_keys
            ):
                with self.assertRaisesRegex(CONFIG.BuildError, 'unmanaged backend'):
                    ADAPTER.operations.select_powerdns_backend_config(
                        native, include, include / '99-hostpanel.conf'
                    )

    def test_failed_dns_build_restores_previous_path_watcher(self):
        commands: list[list[str]] = []

        def failing(*args, **kwargs):
            raise CONFIG.BuildError('doctor failed')

        with mock.patch.object(
            ADAPTER.operations, 'service_active', return_value=True
        ), mock.patch.object(
            ADAPTER, 'run_command',
            side_effect=lambda command, **kwargs: commands.append(command)
            or subprocess.CompletedProcess(command, 0, '', ''),
        ):
            with self.assertRaisesRegex(CONFIG.BuildError, 'doctor failed'):
                ADAPTER.guarded_apply_build(
                    failing, 'all', {}, mock.Mock(), pathlib.Path('/tmp/log'),
                    pathlib.Path('/tmp/backup'), pathlib.Path('/tmp/python'),
                    pathlib.Path('/tmp/doctor'), {'dns'}, pathlib.Path('/tmp/web'),
                    pathlib.Path('/tmp/mode'),
                )
        self.assertIn(
            ['systemctl', 'enable', '--now', 'hostpanel-pdns-zones.path'],
            commands,
        )

    def test_runtime_patcher_routes_core_and_root_helper_idempotently(self):
        spec = importlib.util.spec_from_file_location(
            'patch_powerdns_runtime', TOOLS / 'patch_powerdns_runtime.py'
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        core_source = module.CORE_OLD
        root_source = (
            'RHEL_FAMILY=no\nDNS_UNIT="named"\n'
            + module.ROOT_ANCHOR
            + 'case "$verb" in\n'
            + module.ALLOW_OLD
            + '\n'
            + module.DNSSEC_OLD
        )
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            core = root / 'core.py'
            helper = root / 'hostpanel-root'
            core.write_text(core_source, encoding='utf-8')
            helper.write_text(root_source, encoding='utf-8')
            core.chmod(0o644)
            helper.chmod(0o755)
            with mock.patch.object(module, 'trusted_file', side_effect=lambda item: item.stat()):
                module.patch_core(core)
                module.patch_root(helper)
                first = (core.read_text(), helper.read_text())
                module.patch_core(core)
                module.patch_root(helper)
            self.assertEqual(first, (core.read_text(), helper.read_text()))
            self.assertIn('return "pdns"', first[0])
            self.assertIn('DNS_UNIT="pdns"', first[1])
            self.assertIn('named|pdns|postfix', first[1])
            self.assertIn('requires dns=bind', first[1])
            compile(first[0], str(core), 'exec')

    def test_installer_preserves_applied_dns_mode(self):
        source = (TOOLS / 'install-hostpanel-build.sh').read_text(encoding='utf-8')
        self.assertIn('if [[ ! -e "$DNS_MODE_FILE" ]]; then', source)
        self.assertNotIn('printf \'%s\\n\' "$DNS_MODE" >"$DNS_MODE_FILE"', source)
        self.assertIn('/opt/hostpanel/tools/patch-powerdns-runtime', source)


if __name__ == '__main__':
    unittest.main()
