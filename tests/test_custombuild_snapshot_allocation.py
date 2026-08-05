from __future__ import annotations

import datetime as dt
import os
import pathlib
import stat
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import hostpanel_build_operations_adapter as adapter
from hostpanel_build_config import BuildError


class FixedDateTime(dt.datetime):
    @classmethod
    def now(cls, tz=None):
        value = cls(2026, 8, 5, 9, 30, 0, 123456)
        return value.replace(tzinfo=tz) if tz is not None else value


class ConfigurationSnapshotAllocationTests(unittest.TestCase):
    def snapshot_context(self, source: pathlib.Path):
        return (
            mock.patch.object(
                adapter.operations, 'CONFIG_PATHS',
                {'dns': (str(source),)},
            ),
            mock.patch.object(
                adapter.operations, 'owner_ids',
                return_value=(os.getuid(), os.getgid()),
            ),
            mock.patch.object(adapter.os, 'fchown'),
            mock.patch.object(adapter.dt, 'datetime', FixedDateTime),
        )

    def test_same_timestamp_snapshots_are_unique_and_do_not_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            backup = root / 'backup'
            backup.mkdir(mode=0o700)
            source = root / 'named.conf'
            source.write_text('zone first\n', encoding='utf-8')

            patches = self.snapshot_context(source)
            with patches[0], patches[1], patches[2], patches[3], \
                 mock.patch.object(
                     adapter.secrets, 'token_hex', side_effect=['first', 'second']
                 ):
                first = adapter.config_snapshot('dns', backup)
                self.assertIsNotNone(first)
                first_payload = first.read_bytes()
                source.write_text('zone second\n', encoding='utf-8')
                second = adapter.config_snapshot('dns', backup)

            self.assertIsNotNone(second)
            self.assertNotEqual(first, second)
            self.assertEqual(first.read_bytes(), first_payload)
            self.assertEqual(stat.S_IMODE(first.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(second.stat().st_mode), 0o600)
            with tarfile.open(first, 'r:gz') as archive:
                member = str(source).lstrip('/')
                self.assertEqual(
                    archive.extractfile(member).read(), b'zone first\n'
                )
            with tarfile.open(second, 'r:gz') as archive:
                member = str(source).lstrip('/')
                self.assertEqual(
                    archive.extractfile(member).read(), b'zone second\n'
                )

    def test_existing_candidate_is_skipped_without_truncation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            backup = root / 'backup'
            backup.mkdir(mode=0o700)
            source = root / 'named.conf'
            source.write_text('zone data\n', encoding='utf-8')
            collision = backup / 'dns-20260805T093000.123456Z-same.tar.gz'
            collision.write_bytes(b'keep me')

            patches = self.snapshot_context(source)
            with patches[0], patches[1], patches[2], patches[3], \
                 mock.patch.object(
                     adapter.secrets, 'token_hex', side_effect=['same', 'fresh']
                 ):
                result = adapter.config_snapshot('dns', backup)

            self.assertEqual(collision.read_bytes(), b'keep me')
            self.assertIsNotNone(result)
            self.assertNotEqual(result, collision)
            self.assertTrue(result.name.endswith('-fresh.tar.gz'))

    def test_archive_failure_removes_partial_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            backup = root / 'backup'
            backup.mkdir(mode=0o700)
            source = root / 'named.conf'
            source.write_text('zone data\n', encoding='utf-8')

            patches = self.snapshot_context(source)
            with patches[0], patches[1], patches[2], patches[3], \
                 mock.patch.object(
                     adapter.secrets, 'token_hex', return_value='partial'
                 ), mock.patch.object(
                     adapter.tarfile, 'open', side_effect=OSError('archive failed')
                 ):
                with self.assertRaisesRegex(OSError, 'archive failed'):
                    adapter.config_snapshot('dns', backup)

            self.assertEqual(list(backup.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
