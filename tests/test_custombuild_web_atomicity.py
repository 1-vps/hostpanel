from __future__ import annotations

import importlib.util
import os
import pathlib
import stat
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'


def load_web_module():
    store = types.ModuleType('store')
    store.connect = lambda: None
    modules = types.ModuleType('modules')
    modules.webserver = types.SimpleNamespace()
    previous_store = sys.modules.get('store')
    previous_modules = sys.modules.get('modules')
    sys.modules['store'] = store
    sys.modules['modules'] = modules
    try:
        name = 'hostpanel_build_web_atomicity_test'
        spec = importlib.util.spec_from_file_location(
            name, TOOLS / 'hostpanel_build_web.py'
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        if previous_store is None:
            sys.modules.pop('store', None)
        else:
            sys.modules['store'] = previous_store
        if previous_modules is None:
            sys.modules.pop('modules', None)
        else:
            sys.modules['modules'] = previous_modules


WEB = load_web_module()
BuildError = WEB.BuildError


class CustomBuildWebAtomicityTests(unittest.TestCase):
    @staticmethod
    def metadata(mode: int = 0o640):
        return types.SimpleNamespace(
            st_mode=stat.S_IFREG | mode,
            st_uid=os.getuid(),
            st_gid=os.getgid(),
            st_nlink=1,
        )

    def test_existing_config_preserves_xattrs_and_fsyncs_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            target = root / 'httpd_config.conf'
            target.write_text('old\n', encoding='utf-8')
            applied: list[tuple[pathlib.Path, dict[str, bytes]]] = []
            with mock.patch.object(
                WEB, 'trusted_root_file', return_value=self.metadata()
            ), mock.patch.object(
                WEB, '_capture_xattrs',
                return_value={'security.selinux': b'hostpanel'},
            ), mock.patch.object(
                WEB, '_apply_xattrs',
                side_effect=lambda path, values: applied.append(
                    (path, dict(values))
                ),
            ), mock.patch.object(
                WEB, '_fsync_parent'
            ) as fsync_parent:
                WEB.write_atomic(target, 'new\n')
            self.assertEqual(target.read_text(encoding='utf-8'), 'new\n')
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o640)
            self.assertEqual(len(applied), 1)
            self.assertNotEqual(applied[0][0], target)
            self.assertEqual(
                applied[0][1], {'security.selinux': b'hostpanel'}
            )
            fsync_parent.assert_called_once_with(target)
            self.assertEqual(list(root.glob('.httpd_config.conf.hostpanel-build.*')), [])

    def test_failed_config_write_keeps_original_and_removes_random_temp(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            target = root / 'admin_config.conf'
            target.write_text('old\n', encoding='utf-8')
            with mock.patch.object(
                WEB, 'trusted_root_file', return_value=self.metadata()
            ), mock.patch.object(
                WEB, '_capture_xattrs', return_value={}
            ), mock.patch.object(
                WEB.secrets, 'token_hex', return_value='fresh'
            ), mock.patch.object(WEB.os, 'write', return_value=0):
                with self.assertRaisesRegex(BuildError, 'could not write'):
                    WEB.write_atomic(target, 'new\n')
            self.assertEqual(target.read_text(encoding='utf-8'), 'old\n')
            self.assertFalse(
                (root / '.admin_config.conf.hostpanel-build.fresh').exists()
            )

    def test_new_file_sets_metadata_before_publish_and_fsyncs_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory) / 'hostpanel.conf'
            with mock.patch.object(
                WEB.os, 'fchown'
            ) as fchown, mock.patch.object(
                WEB, '_fsync_parent'
            ) as fsync_parent:
                WEB.write_new_root_file(
                    target, 'listener HostPanel {}\n', 0o640, os.getgid()
                )
            self.assertEqual(
                target.read_text(encoding='utf-8'), 'listener HostPanel {}\n'
            )
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o640)
            fchown.assert_called_once()
            fsync_parent.assert_called_once_with(target)

    def test_symlink_replace_and_unlink_are_directory_durable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            binary = root / 'lsphp85'
            binary.write_text('binary', encoding='utf-8')
            link = root / 'lsphp'
            with mock.patch.object(WEB, '_fsync_parent') as fsync_parent:
                WEB._replace_symlink(link, binary)
                self.assertTrue(link.is_symlink())
                self.assertEqual(link.resolve(), binary)
                WEB._unlink_durable(link)
            self.assertFalse(os.path.lexists(link))
            self.assertEqual(
                fsync_parent.call_args_list,
                [mock.call(link), mock.call(link)],
            )
            self.assertEqual(list(root.glob('.lsphp.hostpanel-build.*')), [])

    def test_runtime_snapshots_restore_legacy_symlink_and_state_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            legacy = root / 'legacy-lsphp'
            current = root / 'current-lsphp'
            legacy.write_text('legacy', encoding='utf-8')
            current.write_text('current', encoding='utf-8')
            link = root / 'lsphp'
            link.symlink_to(legacy)
            state = root / 'lsphp-versions'
            state.write_text('74\n', encoding='utf-8')
            state.chmod(0o640)

            with mock.patch.object(
                WEB, 'trusted_root_file', return_value=self.metadata()
            ), mock.patch.object(
                WEB, '_capture_xattrs', return_value={}
            ), mock.patch.object(WEB.os, 'fchown'):
                link_snapshot = WEB._snapshot_path(
                    link, allow_symlink=True
                )
                state_snapshot = WEB._snapshot_path(state)
                WEB._replace_symlink(link, current)
                WEB.write_new_root_file(state, '85\n', 0o644)
                WEB._restore_path(link, link_snapshot)
                WEB._restore_path(state, state_snapshot)

            self.assertTrue(link.is_symlink())
            self.assertEqual(link.resolve(), legacy)
            self.assertEqual(state.read_text(encoding='utf-8'), '74\n')
            self.assertEqual(stat.S_IMODE(state.stat().st_mode), 0o640)
            self.assertEqual(
                list(root.glob('.*.hostpanel-build.*')), []
            )

    def test_runtime_snapshot_restores_legacy_regular_lsphp_wrapper(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            wrapper = root / 'lsphp'
            wrapper.write_text('#!/bin/sh\nexec legacy-lsphp "$@"\n')
            wrapper.chmod(0o755)
            current = root / 'current-lsphp'
            current.write_text('current', encoding='utf-8')

            with mock.patch.object(
                WEB, 'trusted_root_file', return_value=self.metadata(0o755)
            ), mock.patch.object(
                WEB, '_capture_xattrs', return_value={}
            ), mock.patch.object(WEB.os, 'fchown'):
                snapshot = WEB._snapshot_path(
                    wrapper, allow_symlink=True
                )
                WEB._replace_symlink(wrapper, current)
                WEB._restore_path(wrapper, snapshot)

            self.assertFalse(wrapper.is_symlink())
            self.assertEqual(
                wrapper.read_text(encoding='utf-8'),
                '#!/bin/sh\nexec legacy-lsphp "$@"\n',
            )
            self.assertEqual(stat.S_IMODE(wrapper.stat().st_mode), 0o755)

    def test_source_uses_random_temporaries_and_atomic_metadata(self):
        source = (TOOLS / 'hostpanel_build_web.py').read_text(
            encoding='utf-8'
        )
        self.assertNotIn('os.getpid()', source)
        self.assertIn('secrets.token_hex(12)', source)
        self.assertIn('os.fchmod(fd, mode)', source)
        self.assertIn('_capture_xattrs(path)', source)
        self.assertIn('_fsync_parent(path)', source)
        self.assertIn('_unlink_durable(OLS_REGISTRY)', source)
        self.assertIn('lsphp_link_snapshot = _snapshot_path', source)
        self.assertIn('lsphp_state_snapshot = _snapshot_path', source)
        self.assertIn('_restore_path(path, snapshot)', source)


if __name__ == '__main__':
    unittest.main()
