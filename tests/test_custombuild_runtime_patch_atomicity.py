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


if __name__ == '__main__':
    unittest.main()
