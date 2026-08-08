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
    @staticmethod
    def _load_runtime_patcher():
        spec = importlib.util.spec_from_file_location(
            'patch_powerdns_runtime', TOOLS / 'patch_powerdns_runtime.py'
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_parser_accepts_whitespace_and_append_forms(self):
        text = """
include-dir = /etc/powerdns/pdns.d
launch = bind
launch += gsqlite3
"""
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
            ), mock.patch.object(ADAPTER, 'prepare_include_directory'):
                with self.assertRaisesRegex(CONFIG.BuildError, 'unmanaged backend'):
                    ADAPTER.operations.select_powerdns_backend_config(
                        native, include, include / '99-hostpanel.conf'
                    )

    def test_failed_powerdns_build_restores_previous_path_watcher(self):
        commands: list[list[str]] = []

        def failing(*args, **kwargs):
            raise CONFIG.BuildError('doctor failed')

        with mock.patch.object(
            ADAPTER, 'applied_dns_mode', side_effect=['powerdns', 'powerdns']
        ), mock.patch.object(
            ADAPTER.operations, 'service_active', return_value=True
        ), mock.patch.object(
            ADAPTER, 'run_command',
            side_effect=lambda command, **kwargs: commands.append(command)
            or subprocess.CompletedProcess(command, 0, '', ''),
        ):
            with self.assertRaisesRegex(CONFIG.BuildError, 'doctor failed'):
                ADAPTER.guarded_apply_build(
                    failing, 'all', {'dns': 'powerdns'}, mock.Mock(),
                    pathlib.Path('/tmp/log'), pathlib.Path('/tmp/backup'),
                    pathlib.Path('/tmp/python'), pathlib.Path('/tmp/doctor'),
                    {'dns'}, pathlib.Path('/tmp/web'), pathlib.Path('/tmp/mode'),
                )
        self.assertIn(
            ['systemctl', 'enable', '--now', 'hostpanel-pdns-zones.path'],
            commands,
        )

    def test_runtime_patcher_routes_core_and_root_helper_idempotently(self):
        module = self._load_runtime_patcher()
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

    def test_runtime_patcher_rolls_back_core_when_root_write_fails(self):
        module = self._load_runtime_patcher()
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
            app_root = pathlib.Path(directory)
            core = app_root / 'core.py'
            helper = app_root / 'hostpanel-root'
            core.write_text(core_source, encoding='utf-8')
            helper.write_text(root_source, encoding='utf-8')
            core.chmod(0o644)
            helper.chmod(0o755)
            real_write = module.write_atomic

            def fail_root(path, text, metadata):
                if path == helper:
                    raise OSError('root helper write failed')
                return real_write(path, text, metadata)

            with mock.patch.object(module, 'APP_ROOT', app_root), \
                 mock.patch.object(module.os, 'geteuid', return_value=0), \
                 mock.patch.object(
                     module, 'trusted_file', side_effect=lambda item: item.stat()
                 ), mock.patch.object(
                     module, 'write_atomic', side_effect=fail_root
                 ):
                with self.assertRaisesRegex(
                    OSError, 'root helper write failed'
                ):
                    module.main()
            self.assertEqual(core.read_text(encoding='utf-8'), core_source)
            self.assertEqual(helper.read_text(encoding='utf-8'), root_source)

    def test_runtime_patcher_restores_both_after_root_parent_fsync_failure(self):
        module = self._load_runtime_patcher()
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
            app_root = pathlib.Path(directory)
            core = app_root / 'core.py'
            helper = app_root / 'hostpanel-root'
            core.write_text(core_source, encoding='utf-8')
            helper.write_text(root_source, encoding='utf-8')
            core.chmod(0o644)
            helper.chmod(0o755)
            synced: list[pathlib.Path] = []

            def fail_second_sync(path: pathlib.Path) -> None:
                synced.append(path)
                if len(synced) == 2:
                    raise OSError('root directory fsync failed')

            with mock.patch.object(module, 'APP_ROOT', app_root), \
                 mock.patch.object(module.os, 'geteuid', return_value=0), \
                 mock.patch.object(
                     module, 'trusted_file', side_effect=lambda item: item.stat()
                 ), mock.patch.object(
                     module, '_fsync_parent', side_effect=fail_second_sync
                 ):
                with self.assertRaisesRegex(
                    OSError, 'root directory fsync failed'
                ):
                    module.main()
            self.assertEqual(core.read_text(encoding='utf-8'), core_source)
            self.assertEqual(helper.read_text(encoding='utf-8'), root_source)
            self.assertEqual(synced, [core, helper, helper, core])

    def test_runtime_patcher_prevalidates_both_shapes_before_first_write(self):
        module = self._load_runtime_patcher()
        with tempfile.TemporaryDirectory() as directory:
            app_root = pathlib.Path(directory)
            core = app_root / 'core.py'
            helper = app_root / 'hostpanel-root'
            core.write_text(module.CORE_OLD, encoding='utf-8')
            helper.write_text('unexpected root helper\n', encoding='utf-8')
            core.chmod(0o644)
            helper.chmod(0o755)
            with mock.patch.object(module, 'APP_ROOT', app_root), \
                 mock.patch.object(module.os, 'geteuid', return_value=0), \
                 mock.patch.object(
                     module, 'trusted_file', side_effect=lambda item: item.stat()
                 ), mock.patch.object(module, 'write_atomic') as writer:
                with self.assertRaisesRegex(
                    SystemExit, 'unexpected root DNS mode shape'
                ):
                    module.main()
            writer.assert_not_called()
            self.assertEqual(core.read_text(encoding='utf-8'), module.CORE_OLD)

    def test_installer_preserves_applied_dns_mode(self):
        source = (TOOLS / 'install-hostpanel-build.sh').read_text(encoding='utf-8')
        self.assertIn('ensure_mode_file "$DNS_MODE_FILE" bind', source)
        self.assertNotIn("printf '%s\\n' \"$DNS_MODE\" >\"$DNS_MODE_FILE\"", source)
        self.assertIn('/opt/hostpanel/tools/patch-powerdns-runtime', source)


if __name__ == '__main__':
    unittest.main()
