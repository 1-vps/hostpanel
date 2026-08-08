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

import hostpanel_build_extras as EXTRAS
from hostpanel_build_config import BuildError


class DurabilityAuditTests(unittest.TestCase):
    @staticmethod
    def load_patcher(filename: str):
        name = 'durability_' + filename.replace('.', '_')
        spec = importlib.util.spec_from_file_location(name, TOOLS / filename)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    def test_all_runtime_patchers_fsync_parent_after_replace(self):
        cases = (
            ('patch_custombuild_runtime.py', False),
            ('patch_powerdns_runtime.py', True),
            ('patch_varnish_runtime.py', True),
            ('patch_extras_doctor.py', True),
        )
        for filename, accepts_metadata in cases:
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as directory:
                module = self.load_patcher(filename)
                target = pathlib.Path(directory) / 'runtime'
                target.write_text('old\n', encoding='utf-8')
                metadata = target.stat()
                with mock.patch.object(
                    module, 'copy_xattrs'
                ), mock.patch.object(
                    module.os, 'fchown'
                ), mock.patch.object(
                    module, '_fsync_parent'
                ) as fsync_parent:
                    if accepts_metadata:
                        module.write_atomic(target, 'new\n', metadata)
                    else:
                        module.write_atomic(target, 'new\n')
                self.assertEqual(
                    target.read_text(encoding='utf-8'), 'new\n'
                )
                fsync_parent.assert_called_once_with(target)

    def test_custombuild_runtime_main_rolls_back_first_file_when_second_fails(self):
        module = self.load_patcher('patch_custombuild_runtime.py')
        with tempfile.TemporaryDirectory() as directory:
            app = pathlib.Path(directory)
            (app / 'modules').mkdir()
            webserver = app / 'modules/webserver.py'
            main = app / 'main.py'
            doctor = app / 'hostpanel-doctor'
            originals = {
                webserver: 'web-original\n',
                main: 'main-original\n',
                doctor: 'doctor-original\n',
            }
            for path, content in originals.items():
                path.write_text(content, encoding='utf-8')

            def patch_webserver(path: pathlib.Path) -> None:
                path.write_text('web-mutated\n', encoding='utf-8')

            def patch_main(_path: pathlib.Path) -> None:
                raise BuildError('injected second-file failure')

            with mock.patch.object(module, 'APP_ROOT', app), \
                 mock.patch.object(module.os, 'geteuid', return_value=0), \
                 mock.patch.object(module, 'trusted_file'), \
                 mock.patch.object(
                     module, 'patch_webserver', side_effect=patch_webserver
                 ), mock.patch.object(
                     module, 'patch_main', side_effect=patch_main
                 ), mock.patch.object(module, 'patch_doctor') as patch_doctor, \
                 mock.patch.object(module, 'copy_xattrs'), \
                 mock.patch.object(module.os, 'fchown'):
                with self.assertRaisesRegex(
                    BuildError, 'injected second-file failure'
                ):
                    module.main()

            patch_doctor.assert_not_called()
            for path, content in originals.items():
                self.assertEqual(path.read_text(encoding='utf-8'), content)

    def test_varnish_off_accepts_only_explicit_inactive_state(self):
        options = {'varnish': 'off', 'webserver': 'nginx_apache'}
        inactive = subprocess.CompletedProcess(
            [], 3, 'inactive\n', ''
        )
        with mock.patch.object(
            EXTRAS, '_trusted_varnish_mode', return_value='off'
        ), mock.patch.object(
            EXTRAS, 'run_command', return_value=inactive
        ), mock.patch.object(
            EXTRAS, 'managed_proxy_files', return_value=[]
        ):
            EXTRAS.validate_varnish(options, pathlib.Path('/tmp/build.log'))

    def test_varnish_off_rejects_active_empty_and_query_errors(self):
        options = {'varnish': 'off', 'webserver': 'nginx_apache'}
        cases = (
            (
                subprocess.CompletedProcess([], 0, 'active\n', ''),
                'Varnish service is active',
            ),
            (
                subprocess.CompletedProcess([], 3, '', ''),
                'could not determine active state',
            ),
            (
                subprocess.CompletedProcess(
                    [], 1, '', 'Failed to connect to bus\n'
                ),
                'Failed to connect to bus',
            ),
        )
        for completed, message in cases:
            with self.subTest(message=message), mock.patch.object(
                EXTRAS, '_trusted_varnish_mode', return_value='off'
            ), mock.patch.object(
                EXTRAS, 'run_command', return_value=completed
            ), mock.patch.object(
                EXTRAS, 'managed_proxy_files', return_value=[]
            ):
                with self.assertRaisesRegex(BuildError, message):
                    EXTRAS.validate_varnish(
                        options, pathlib.Path('/tmp/build.log')
                    )


if __name__ == '__main__':
    unittest.main()
