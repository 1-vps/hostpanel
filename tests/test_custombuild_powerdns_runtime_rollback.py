from __future__ import annotations

import importlib.util
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'


class PowerDnsRuntimeRollbackTests(unittest.TestCase):
    @staticmethod
    def load_patcher():
        spec = importlib.util.spec_from_file_location(
            'rollback_patch_powerdns_runtime',
            TOOLS / 'patch_powerdns_runtime.py',
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_root_rollback_fsync_error_still_restores_core(self):
        module = self.load_patcher()
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

            def fail_root_syncs(path: pathlib.Path) -> None:
                synced.append(path)
                if len(synced) == 2:
                    raise OSError('forward root directory fsync failed')
                if len(synced) == 3:
                    raise OSError('rollback root directory fsync failed')

            with mock.patch.object(module, 'APP_ROOT', app_root), \
                 mock.patch.object(module.os, 'geteuid', return_value=0), \
                 mock.patch.object(
                     module,
                     'trusted_file',
                     side_effect=lambda item: item.stat(),
                 ), mock.patch.object(
                     module,
                     '_fsync_parent',
                     side_effect=fail_root_syncs,
                 ):
                with self.assertRaisesRegex(
                    SystemExit,
                    'root-helper rollback failed: '
                    'rollback root directory fsync failed',
                ):
                    module.main()

            self.assertEqual(core.read_text(encoding='utf-8'), core_source)
            self.assertEqual(helper.read_text(encoding='utf-8'), root_source)
            self.assertEqual(synced, [core, helper, helper, core])


if __name__ == '__main__':
    unittest.main()
