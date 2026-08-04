from __future__ import annotations

import importlib.util
import pathlib
import stat
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
sys.path.insert(0, str(TOOLS))

import hostpanel_build_extras as EXTRAS
import hostpanel_build_extras_state as STATE
from hostpanel_build_config import BuildError


class WrapperFollowupTests(unittest.TestCase):
    def test_extras_wrapper_forwards_monkeypatches(self):
        replacement = mock.Mock(name='run_command')
        with mock.patch.object(EXTRAS, 'run_command', replacement):
            self.assertIs(EXTRAS._IMPL.run_command, replacement)
        root = pathlib.Path('/tmp/forwarded-nginx')
        with mock.patch.object(EXTRAS, 'NGINX_AVAILABLE', root):
            self.assertEqual(EXTRAS._IMPL.NGINX_AVAILABLE, root)

    def test_spec_loaded_patchers_do_not_require_registration(self):
        for filename in (
            'patch_powerdns_runtime.py',
            'patch_varnish_runtime.py',
            'patch_extras_doctor.py',
        ):
            with self.subTest(filename=filename):
                name = 'unregistered_' + filename.replace('.', '_')
                sys.modules.pop(name, None)
                spec = importlib.util.spec_from_file_location(
                    name, TOOLS / filename
                )
                assert spec and spec.loader
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                self.assertNotIn(name, sys.modules)
                self.assertTrue(callable(module.main))

    def test_extras_atomic_writer_preserves_exact_xattrs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            target = root / 'config'
            target.write_bytes(b'old')
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
            with mock.patch.object(EXTRAS, 'require_root'), \
                 mock.patch.object(
                     EXTRAS.pathlib.Path, 'lstat', autospec=True,
                     side_effect=trusted_lstat,
                 ), mock.patch.object(
                     EXTRAS, '_capture_xattrs',
                     return_value={'security.selinux': b'original'},
                 ), mock.patch.object(
                     EXTRAS, '_apply_xattrs',
                     side_effect=lambda path, values: applied.append(
                         (path, dict(values))
                     ),
                 ), mock.patch.object(EXTRAS.os, 'fsync') as fsync, \
                 mock.patch.object(EXTRAS.os, 'fchown'):
                EXTRAS.write_atomic_bytes(target, b'new', 0o640, 0, 0)
            self.assertEqual(target.read_bytes(), b'new')
            self.assertEqual(len(applied), 1)
            self.assertNotEqual(applied[0][0], target)
            self.assertEqual(
                applied[0][1], {'security.selinux': b'original'}
            )
            self.assertEqual(fsync.call_count, 2)

    def test_new_extras_file_keeps_filesystem_security_context(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            target = root / 'config'
            real_lstat = pathlib.Path.lstat

            def trusted_lstat(path: pathlib.Path):
                if path == root:
                    return types.SimpleNamespace(
                        st_mode=stat.S_IFDIR | 0o755, st_uid=0
                    )
                return real_lstat(path)

            with mock.patch.object(EXTRAS, 'require_root'), \
                 mock.patch.object(
                     EXTRAS.pathlib.Path, 'lstat', autospec=True,
                     side_effect=trusted_lstat,
                 ), mock.patch.object(
                     EXTRAS, '_capture_xattrs'
                 ) as capture, mock.patch.object(
                     EXTRAS, '_apply_xattrs'
                 ) as apply_xattrs, mock.patch.object(
                     EXTRAS.os, 'fsync'
                 ) as fsync, mock.patch.object(EXTRAS.os, 'fchown'):
                EXTRAS.write_atomic_bytes(target, b'new', 0o640, 0, 0)
            self.assertEqual(target.read_bytes(), b'new')
            capture.assert_not_called()
            apply_xattrs.assert_not_called()
            self.assertEqual(fsync.call_count, 2)

    def test_extras_atomic_writer_rejects_writable_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            target = root / 'config'
            target.write_bytes(b'unsafe')
            real_lstat = pathlib.Path.lstat

            def trusted_lstat(path: pathlib.Path):
                if path == root:
                    return types.SimpleNamespace(
                        st_mode=stat.S_IFDIR | 0o755, st_uid=0
                    )
                if path == target:
                    return types.SimpleNamespace(
                        st_mode=stat.S_IFREG | 0o666,
                        st_uid=0, st_gid=0, st_nlink=1,
                    )
                return real_lstat(path)

            with mock.patch.object(EXTRAS, 'require_root'), \
                 mock.patch.object(
                     EXTRAS.pathlib.Path, 'lstat', autospec=True,
                     side_effect=trusted_lstat,
                 ), mock.patch.object(EXTRAS, '_open_temporary') as opened:
                with self.assertRaisesRegex(BuildError, 'unsafe configuration file'):
                    EXTRAS.write_atomic_bytes(target, b'new', 0o640, 0, 0)
            opened.assert_not_called()
            self.assertEqual(target.read_bytes(), b'unsafe')

    def test_extras_atomic_failure_removes_random_temporary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            target = root / 'config'
            target.write_bytes(b'old')
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

            with mock.patch.object(EXTRAS, 'require_root'), \
                 mock.patch.object(
                     EXTRAS.pathlib.Path, 'lstat', autospec=True,
                     side_effect=trusted_lstat,
                 ), mock.patch.object(
                     EXTRAS, '_capture_xattrs', return_value={}
                 ), mock.patch.object(
                     EXTRAS.secrets, 'token_hex', return_value='fresh'
                 ), mock.patch.object(EXTRAS.os, 'write', return_value=0):
                with self.assertRaisesRegex(BuildError, 'could not write'):
                    EXTRAS.write_atomic_bytes(target, b'new', 0o640, 0, 0)
            self.assertEqual(target.read_bytes(), b'old')
            self.assertFalse(
                (root / '.config.hostpanel-build.fresh').exists()
            )

    def test_state_snapshot_restores_original_xattrs(self):
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory) / 'config'
            target.write_bytes(b'original')
            metadata = types.SimpleNamespace(
                st_mode=stat.S_IFREG | 0o640,
                st_uid=0, st_gid=0, st_nlink=1,
            )
            original_xattrs = {
                'security.selinux': b'original-label',
                'system.posix_acl_access': b'original-acl',
            }
            with mock.patch.object(
                STATE._IMPL, '_trusted_config', return_value=metadata
            ), mock.patch.object(
                STATE._IMPL.base, '_capture_xattrs',
                return_value=original_xattrs,
            ):
                captured = STATE._capture(target)
            self.assertIsNotNone(captured)
            target.write_bytes(b'candidate')
            restored: list[dict[str, bytes]] = []

            def write(path, payload, mode, uid, gid):
                path.write_bytes(payload)

            with mock.patch.object(
                STATE._IMPL.base, 'write_atomic_bytes', side_effect=write
            ), mock.patch.object(
                STATE._IMPL.base, '_apply_xattrs',
                side_effect=lambda path, values: restored.append(dict(values)),
            ):
                STATE._restore(target, captured)
            self.assertEqual(target.read_bytes(), b'original')
            self.assertEqual(restored, [original_xattrs])

    def test_legacy_state_snapshot_does_not_clear_xattrs(self):
        target = pathlib.Path('/tmp/legacy-state-config')
        legacy = (b'legacy', 0o640, 0, 0)
        with mock.patch.object(
            STATE._IMPL.base, 'write_atomic_bytes'
        ) as write, mock.patch.object(
            STATE._IMPL.base, '_apply_xattrs'
        ) as apply_xattrs:
            STATE._restore(target, legacy)
        write.assert_called_once_with(target, b'legacy', 0o640, 0, 0)
        apply_xattrs.assert_not_called()

    def test_workflow_tracks_preserved_sources(self):
        workflow = (
            ROOT / '.github/workflows/automatic-installer.yml'
        ).read_text(encoding='utf-8')
        self.assertEqual(workflow.count("'tools/*_impl.py'"), 2)
        self.assertEqual(workflow.count("'tests/_custombuild*.py'"), 2)
        self.assertIn('python3 -m compileall -q tools tests', workflow)


if __name__ == '__main__':
    unittest.main()
