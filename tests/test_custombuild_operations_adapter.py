from __future__ import annotations

import os
import pathlib
import stat
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
sys.path.insert(0, str(TOOLS))

import hostpanel_build_operations as OPERATIONS
import hostpanel_build_operations_adapter as ADAPTER
from hostpanel_build_config import BuildError


class CustomBuildOperationsAdapterTests(unittest.TestCase):
    def test_service_probe_requires_explicit_systemd_state(self):
        cases = (
            (
                subprocess.CompletedProcess([], 0, 'active\n', ''),
                True,
            ),
            (
                subprocess.CompletedProcess([], 3, 'inactive\n', ''),
                False,
            ),
        )
        for completed, expected in cases:
            with self.subTest(returncode=completed.returncode), mock.patch.object(
                OPERATIONS, 'run_command', return_value=completed
            ):
                self.assertIs(ADAPTER.service_active('demo.service'), expected)

        for completed in (
            subprocess.CompletedProcess([], 3, '', ''),
            subprocess.CompletedProcess(
                [], 1, '', 'Failed to connect to bus\n'
            ),
        ):
            with self.subTest(stderr=completed.stderr), mock.patch.object(
                OPERATIONS, 'run_command', return_value=completed
            ):
                with self.assertRaisesRegex(
                    BuildError, 'could not determine active state'
                ):
                    ADAPTER.service_active('demo.service')

    def test_mask_failure_restarts_previously_active_service(self):
        calls: list[list[str]] = []

        def run(command, **kwargs):
            calls.append(command)
            if command[:3] == ['systemctl', 'mask', '--runtime']:
                raise BuildError('injected mask failure')
            return subprocess.CompletedProcess(command, 0, '', '')

        with mock.patch.object(
            ADAPTER, 'service_active', return_value=True
        ), mock.patch.object(
            OPERATIONS, 'run_command', side_effect=run
        ):
            with self.assertRaisesRegex(BuildError, 'injected mask failure'):
                ADAPTER.mask_service(
                    'demo.service', pathlib.Path('/tmp/build.log')
                )

        self.assertEqual(
            calls,
            [
                ['systemctl', 'stop', 'demo.service'],
                ['systemctl', 'mask', '--runtime', 'demo.service'],
                ['systemctl', 'start', 'demo.service'],
            ],
        )

    def test_atomic_write_preserves_xattrs_and_fsyncs_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            target = root / 'config'
            target.write_text('old\n', encoding='utf-8')
            target.chmod(0o640)
            real_lstat = pathlib.Path.lstat

            def trusted_lstat(path: pathlib.Path):
                if path == root:
                    return types.SimpleNamespace(
                        st_mode=stat.S_IFDIR | 0o755, st_uid=0
                    )
                if path == target:
                    return types.SimpleNamespace(
                        st_mode=stat.S_IFREG | 0o640,
                        st_uid=0, st_gid=0, st_nlink=1,
                    )
                return real_lstat(path)

            applied: list[tuple[pathlib.Path, dict[str, bytes]]] = []
            with mock.patch.object(OPERATIONS, 'require_root'), \
                 mock.patch.object(
                     ADAPTER.pathlib.Path, 'lstat', autospec=True,
                     side_effect=trusted_lstat,
                 ), mock.patch.object(
                     ADAPTER, '_capture_xattrs',
                     return_value={'security.selinux': b'config-label'},
                 ), mock.patch.object(
                     ADAPTER, '_apply_xattrs',
                     side_effect=lambda path, values: applied.append(
                         (path, dict(values))
                     ),
                 ), mock.patch.object(ADAPTER.os, 'fchown'), \
                 mock.patch.object(
                     ADAPTER, '_fsync_parent'
                 ) as fsync_parent:
                ADAPTER.write_atomic_root(target, 'new\n', 0o640)

            self.assertEqual(target.read_text(encoding='utf-8'), 'new\n')
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o640)
            self.assertEqual(len(applied), 1)
            self.assertNotEqual(applied[0][0], target)
            self.assertEqual(
                applied[0][1], {'security.selinux': b'config-label'}
            )
            fsync_parent.assert_called_once_with(target)

    def test_atomic_failure_keeps_original_and_cleans_random_temp(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            target = root / 'config'
            target.write_text('old\n', encoding='utf-8')
            real_lstat = pathlib.Path.lstat

            def trusted_lstat(path: pathlib.Path):
                if path == root:
                    return types.SimpleNamespace(
                        st_mode=stat.S_IFDIR | 0o755, st_uid=0
                    )
                if path == target:
                    return types.SimpleNamespace(
                        st_mode=stat.S_IFREG | 0o640,
                        st_uid=0, st_gid=0, st_nlink=1,
                    )
                return real_lstat(path)

            with mock.patch.object(OPERATIONS, 'require_root'), \
                 mock.patch.object(
                     ADAPTER.pathlib.Path, 'lstat', autospec=True,
                     side_effect=trusted_lstat,
                 ), mock.patch.object(
                     ADAPTER, '_capture_xattrs', return_value={}
                 ), mock.patch.object(
                     ADAPTER.secrets, 'token_hex', return_value='fresh'
                 ), mock.patch.object(ADAPTER.os, 'write', return_value=0):
                with self.assertRaisesRegex(BuildError, 'could not write'):
                    ADAPTER.write_atomic_root(target, 'new\n', 0o640)

            self.assertEqual(target.read_text(encoding='utf-8'), 'old\n')
            self.assertFalse(
                (root / '.config.hostpanel-build.fresh').exists()
            )

    def test_install_replaces_shared_operation_boundaries(self):
        with mock.patch.object(OPERATIONS, 'service_active') as active, \
             mock.patch.object(OPERATIONS, 'write_atomic_root') as writer, \
             mock.patch.object(OPERATIONS, 'mask_service') as masker:
            ADAPTER.install()
            self.assertIs(OPERATIONS.service_active, ADAPTER.service_active)
            self.assertIs(OPERATIONS.write_atomic_root, ADAPTER.write_atomic_root)
            self.assertIs(OPERATIONS.mask_service, ADAPTER.mask_service)
        self.assertIsNot(active, ADAPTER.service_active)
        self.assertIsNot(writer, ADAPTER.write_atomic_root)
        self.assertIsNot(masker, ADAPTER.mask_service)

    def test_installer_tracks_operations_adapter(self):
        installer = (
            TOOLS / 'install-hostpanel-build.sh'
        ).read_text(encoding='utf-8')
        self.assertEqual(
            installer.count('hostpanel_build_operations_adapter.py'), 1
        )


if __name__ == '__main__':
    unittest.main()
