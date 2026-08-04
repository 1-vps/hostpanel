from __future__ import annotations

import importlib.util
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'


class RuntimePatcherAtomicityTests(unittest.TestCase):
    def load(self, filename: str):
        name = 'atomic_' + filename.replace('.', '_')
        spec = importlib.util.spec_from_file_location(name, TOOLS / filename)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    def test_failed_runtime_patch_writes_leave_original_and_no_temp(self):
        cases = (
            ('patch_powerdns_runtime.py', '.runtime.powerdns.*'),
            ('patch_varnish_runtime.py', '.runtime.varnish.*'),
            ('patch_extras_doctor.py', '.runtime.extras.*'),
        )
        for filename, pattern in cases:
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as directory:
                module = self.load(filename)
                root = pathlib.Path(directory)
                target = root / 'runtime'
                target.write_text('original\n', encoding='utf-8')
                metadata = target.stat()
                with mock.patch.object(module.os, 'write', return_value=0):
                    with self.assertRaisesRegex(SystemExit, 'could not write'):
                        module.write_atomic(target, 'replacement\n', metadata)
                self.assertEqual(
                    target.read_text(encoding='utf-8'), 'original\n'
                )
                self.assertEqual(list(root.glob(pattern)), [])

    def test_powerdns_runtime_write_fsyncs_parent_after_replace(self):
        module = self.load('patch_powerdns_runtime.py')
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            target = root / 'runtime'
            target.write_text('original\n', encoding='utf-8')
            metadata = target.stat()
            with mock.patch.object(module, '_fsync_parent') as fsync_parent:
                module.write_atomic(target, 'replacement\n', metadata)
            self.assertEqual(
                target.read_text(encoding='utf-8'), 'replacement\n'
            )
            fsync_parent.assert_called_once_with(target)

    def test_failed_custombuild_patch_write_preserves_original_and_metadata(self):
        module = self.load('patch_custombuild_runtime.py')
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            target = root / 'runtime'
            target.write_text('original\n', encoding='utf-8')
            target.chmod(0o640)
            before = target.stat()
            with mock.patch.object(module.os, 'write', return_value=0):
                with self.assertRaisesRegex(SystemExit, 'could not write'):
                    module.write_atomic(target, 'replacement\n')
            after = target.stat()
            self.assertEqual(target.read_text(encoding='utf-8'), 'original\n')
            self.assertEqual(after.st_uid, before.st_uid)
            self.assertEqual(after.st_gid, before.st_gid)
            self.assertEqual(after.st_mode, before.st_mode)
            self.assertEqual(list(root.glob('.runtime.custombuild.*')), [])


if __name__ == '__main__':
    unittest.main()
