from __future__ import annotations

import pathlib
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
sys.path.insert(0, str(TOOLS))

import hostpanel_build_extras as EXTRAS


class TextOwnershipTests(unittest.TestCase):
    def test_existing_text_file_preserves_uid_and_gid(self):
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory) / 'mongod.conf'
            target.write_text('old\n', encoding='utf-8')
            metadata = types.SimpleNamespace(st_uid=0, st_gid=4242)
            with mock.patch.object(
                EXTRAS.pathlib.Path, 'lstat', autospec=True,
                return_value=metadata,
            ), mock.patch.object(EXTRAS, 'write_atomic_bytes') as writer:
                EXTRAS.write_atomic_text(target, 'new\n', 0o640)
            writer.assert_called_once_with(
                target, b'new\n', 0o640, 0, 4242
            )

    def test_new_text_file_defaults_to_root_ownership(self):
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory) / 'new.conf'
            with mock.patch.object(EXTRAS, 'write_atomic_bytes') as writer:
                EXTRAS.write_atomic_text(target, 'new\n', 0o640)
            writer.assert_called_once_with(
                target, b'new\n', 0o640, 0, 0
            )


if __name__ == '__main__':
    unittest.main()
