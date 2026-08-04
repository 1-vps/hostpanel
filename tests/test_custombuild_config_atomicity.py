from __future__ import annotations

import os
import pathlib
import stat
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import hostpanel_build_config as config
from hostpanel_build_config import BuildError


class CustomBuildConfigAtomicityTests(unittest.TestCase):
    def options(self) -> dict[str, str]:
        values = dict(config.DEFAULT_OPTIONS)
        values['dns'] = 'powerdns'
        return values

    def test_existing_metadata_is_applied_before_replace_and_parent_fsync(self) -> None:
        events: list[str] = []
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            target = root / 'build.conf'
            target.write_text(
                config.render_config(config.DEFAULT_OPTIONS), encoding='utf-8'
            )
            target.chmod(0o600)
            original_replace = os.replace

            def apply_xattrs(path: pathlib.Path, values: dict[str, bytes]) -> None:
                self.assertNotEqual(path, target)
                self.assertEqual(values, {'security.selinux': b'context'})
                events.append('xattrs')

            def replace(source, destination) -> None:
                events.append('replace')
                original_replace(source, destination)

            with mock.patch.object(
                config, 'owner_ids', return_value=(os.getuid(), os.getgid())
            ), mock.patch.object(
                config, '_capture_xattrs',
                return_value={'security.selinux': b'context'},
            ), mock.patch.object(
                config, '_apply_xattrs', side_effect=apply_xattrs
            ), mock.patch.object(
                config, '_apply_expected_selinux_context'
            ) as selinux, mock.patch.object(config.os, 'fchown'), \
                 mock.patch.object(config.os, 'replace', side_effect=replace), \
                 mock.patch.object(
                     config, '_fsync_parent',
                     side_effect=lambda path: events.append('parent-fsync'),
                 ), mock.patch.object(config.os, 'chmod') as chmod, \
                 mock.patch.object(config.os, 'chown') as chown:
                config.write_config(target, self.options())

            self.assertLess(events.index('xattrs'), events.index('replace'))
            self.assertEqual(events[-1], 'parent-fsync')
            selinux.assert_not_called()
            chmod.assert_not_called()
            chown.assert_not_called()
            self.assertEqual(config.read_config(target)['dns'], 'powerdns')

    def test_new_file_uses_final_path_selinux_context_before_replace(self) -> None:
        events: list[str] = []
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory) / 'build.conf'
            original_replace = os.replace

            def apply_selinux(fd: int, path: pathlib.Path, mode: int) -> None:
                self.assertGreaterEqual(fd, 0)
                self.assertEqual(path, target)
                self.assertEqual(mode, 0o600)
                events.append('selinux')

            def replace(source, destination) -> None:
                events.append('replace')
                original_replace(source, destination)

            with mock.patch.object(
                config, 'owner_ids', return_value=(os.getuid(), os.getgid())
            ), mock.patch.object(config.os, 'fchown'), mock.patch.object(
                config, '_apply_expected_selinux_context',
                side_effect=apply_selinux,
            ) as selinux, mock.patch.object(
                config, '_apply_xattrs'
            ) as xattrs, mock.patch.object(
                config.os, 'replace', side_effect=replace
            ), mock.patch.object(config, '_fsync_parent'):
                config.write_config(target, self.options())

            selinux.assert_called_once()
            xattrs.assert_not_called()
            self.assertLess(events.index('selinux'), events.index('replace'))
            self.assertEqual(config.read_config(target)['dns'], 'powerdns')

    def test_short_writes_are_completed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory) / 'build.conf'
            original_write = os.write
            calls = 0

            def short_write(fd: int, payload) -> int:
                nonlocal calls
                calls += 1
                view = memoryview(payload)
                length = max(1, len(view) // 2)
                return original_write(fd, view[:length])

            with mock.patch.object(
                config, 'owner_ids', return_value=(os.getuid(), os.getgid())
            ), mock.patch.object(config.os, 'write', side_effect=short_write), \
                 mock.patch.object(config.os, 'fchown'), mock.patch.object(
                     config, '_apply_expected_selinux_context'
                 ):
                config.write_config(target, self.options())

            self.assertGreater(calls, 1)
            self.assertEqual(config.read_config(target)['dns'], 'powerdns')

    def test_unsafe_existing_target_is_rejected_before_temporary_creation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            real = root / 'real.conf'
            real.write_text('dns=bind\n', encoding='utf-8')
            target = root / 'build.conf'
            target.symlink_to(real)
            with mock.patch.object(
                config, 'owner_ids', return_value=(os.getuid(), os.getgid())
            ), mock.patch.object(config.tempfile, 'mkstemp') as mkstemp:
                with self.assertRaisesRegex(
                    BuildError, 'unsafe build configuration'
                ):
                    config.write_config(target, self.options())
            mkstemp.assert_not_called()
            self.assertTrue(target.is_symlink())

    def test_writer_sets_final_mode_before_metadata_copy(self) -> None:
        source = (TOOLS / 'hostpanel_build_config.py').read_text(encoding='utf-8')
        writer = source.split('def write_config(', 1)[1]
        self.assertLess(writer.index('os.fchmod(fd, 0o600)'), writer.index('_apply_xattrs('))
        self.assertLess(writer.index('_apply_xattrs('), writer.index('os.replace('))
        self.assertLess(
            writer.index('_apply_expected_selinux_context('),
            writer.index('os.replace('),
        )
        self.assertNotIn('os.chmod(path', writer)
        self.assertNotIn('os.chown(path', writer)


if __name__ == '__main__':
    unittest.main()
