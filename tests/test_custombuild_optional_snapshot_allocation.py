from __future__ import annotations

import datetime as dt
import pathlib
import stat
import sys
import tarfile
import tempfile
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import hostpanel_build_extras as extras
from hostpanel_build_config import BuildError


class FixedDateTime(dt.datetime):
    @classmethod
    def now(cls, tz=None):
        value = cls(2026, 8, 5, 10, 0, 0, 654321)
        return value.replace(tzinfo=tz) if tz is not None else value


class OptionalSnapshotAllocationTests(unittest.TestCase):
    def trusted_lstat(self, backup: pathlib.Path):
        real_lstat = pathlib.Path.lstat

        def lstat(path: pathlib.Path):
            if path == backup:
                return types.SimpleNamespace(
                    st_mode=stat.S_IFDIR | 0o700,
                    st_uid=0,
                    st_gid=0,
                )
            return real_lstat(path)

        return lstat

    def test_pid_reuse_cannot_overwrite_existing_optional_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            backup = root / 'backup'
            backup.mkdir(mode=0o700)
            source = root / 'mongod.conf'
            source.write_text('net:\n', encoding='utf-8')
            collision = (
                backup /
                'mongodb-20260805T100000.654321Z-reused.tar.gz'
            )
            collision.write_bytes(b'previous backup')

            with mock.patch.object(
                extras.pathlib.Path, 'lstat', autospec=True,
                side_effect=self.trusted_lstat(backup),
            ), mock.patch.object(extras.os, 'fchown'), \
                 mock.patch.object(extras.dt, 'datetime', FixedDateTime), \
                 mock.patch.object(
                     extras.secrets, 'token_hex',
                     side_effect=['reused', 'fresh'],
                 ):
                result = extras.snapshot_paths(
                    'mongodb', (source,), backup
                )

            self.assertEqual(collision.read_bytes(), b'previous backup')
            self.assertIsNotNone(result)
            self.assertNotEqual(result, collision)
            self.assertTrue(result.name.endswith('-fresh.tar.gz'))
            self.assertEqual(stat.S_IMODE(result.stat().st_mode), 0o600)
            with tarfile.open(result, 'r:gz') as archive:
                member = str(source).lstrip('/')
                self.assertEqual(archive.extractfile(member).read(), b'net:\n')

    def test_optional_archive_failure_removes_partial_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            backup = root / 'backup'
            backup.mkdir(mode=0o700)
            source = root / 'default.vcl'
            source.write_text('vcl 4.1;\n', encoding='utf-8')

            with mock.patch.object(
                extras.pathlib.Path, 'lstat', autospec=True,
                side_effect=self.trusted_lstat(backup),
            ), mock.patch.object(extras.os, 'fchown'), \
                 mock.patch.object(extras.dt, 'datetime', FixedDateTime), \
                 mock.patch.object(
                     extras.secrets, 'token_hex', return_value='partial'
                 ), mock.patch.object(
                     extras.tarfile, 'open', side_effect=OSError('archive failed')
                 ):
                with self.assertRaisesRegex(OSError, 'archive failed'):
                    extras.snapshot_paths('varnish', (source,), backup)

            self.assertEqual(list(backup.iterdir()), [])

    def test_optional_snapshot_rejects_unsafe_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            backup = root / 'backup'
            backup.mkdir(mode=0o700)
            source = root / 'config'
            source.write_text('data\n', encoding='utf-8')
            with mock.patch.object(
                extras.pathlib.Path, 'lstat', autospec=True,
                side_effect=self.trusted_lstat(backup),
            ):
                with self.assertRaisesRegex(
                    BuildError, 'invalid configuration snapshot name'
                ):
                    extras.snapshot_paths('../escape', (source,), backup)


if __name__ == '__main__':
    unittest.main()
