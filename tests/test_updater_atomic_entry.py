from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import shutil
import stat
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'


def load_installed_entry(directory: pathlib.Path):
    entry = directory / 'hostpanel-update'
    implementation = directory / 'hostpanel-update-impl.py'
    shutil.copy2(TOOLS / 'hostpanel-update-entry.py', entry)
    shutil.copy2(TOOLS / 'hostpanel-update.py', implementation)
    name = 'installed_hostpanel_update_entry'
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, entry)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class UpdaterAtomicEntryTests(unittest.TestCase):
    def test_partial_writes_publish_complete_json_and_preserve_xattrs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            runtime = root / 'runtime'
            runtime.mkdir(mode=0o700)
            entry = load_installed_entry(runtime)
            status = root / 'state' / 'update-status.json'
            status.parent.mkdir(mode=0o700)
            status.write_text('{"old": true}\n', encoding='utf-8')
            status.chmod(0o600)
            applied: list[tuple[pathlib.Path, dict[str, bytes]]] = []
            real_write = os.write

            def partial_write(fd: int, payload) -> int:
                return real_write(fd, bytes(payload[:3]))

            with mock.patch.object(
                entry._IMPL, '_owner_ids',
                return_value=(os.getuid(), os.getgid()),
            ), mock.patch.object(
                entry, '_capture_xattrs',
                return_value={'security.selinux': b'hostpanel-status'},
            ), mock.patch.object(
                entry, '_apply_xattrs',
                side_effect=lambda path, values: applied.append(
                    (path, dict(values))
                ),
            ), mock.patch.object(
                entry.os, 'write', side_effect=partial_write
            ), mock.patch.object(entry.os, 'fchown'), \
                 mock.patch.object(entry, '_fsync_parent') as fsync_parent:
                entry.atomic_json(
                    status,
                    {'state': 'current', 'installed_version': '1.2.3'},
                )

            self.assertEqual(
                json.loads(status.read_text(encoding='utf-8')),
                {'state': 'current', 'installed_version': '1.2.3'},
            )
            self.assertEqual(stat.S_IMODE(status.stat().st_mode), 0o600)
            self.assertEqual(len(applied), 1)
            self.assertNotEqual(applied[0][0], status)
            self.assertEqual(
                applied[0][1],
                {'security.selinux': b'hostpanel-status'},
            )
            fsync_parent.assert_called_once_with(status)
            self.assertEqual(
                list(status.parent.glob('.update-status.json.hostpanel-update.*')),
                [],
            )

    def test_failed_write_keeps_existing_status_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            runtime = root / 'runtime'
            runtime.mkdir(mode=0o700)
            entry = load_installed_entry(runtime)
            status = root / 'state' / 'update-status.json'
            status.parent.mkdir(mode=0o700)
            original = '{"state": "old"}\n'
            status.write_text(original, encoding='utf-8')
            status.chmod(0o600)

            with mock.patch.object(
                entry._IMPL, '_owner_ids',
                return_value=(os.getuid(), os.getgid()),
            ), mock.patch.object(
                entry, '_capture_xattrs', return_value={}
            ), mock.patch.object(
                entry.secrets, 'token_hex', return_value='fresh'
            ), mock.patch.object(entry.os, 'write', return_value=0):
                with self.assertRaisesRegex(
                    entry.UpdateError, 'could not write update status'
                ):
                    entry.atomic_json(status, {'state': 'new'})

            self.assertEqual(status.read_text(encoding='utf-8'), original)
            self.assertFalse(
                (status.parent / '.update-status.json.hostpanel-update.fresh').exists()
            )

    def test_status_symlink_is_rejected_without_touching_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            runtime = root / 'runtime'
            runtime.mkdir(mode=0o700)
            entry = load_installed_entry(runtime)
            state = root / 'state'
            state.mkdir(mode=0o700)
            victim = state / 'victim.json'
            victim.write_text('victim\n', encoding='utf-8')
            status = state / 'update-status.json'
            status.symlink_to(victim)

            with mock.patch.object(
                entry._IMPL, '_owner_ids',
                return_value=(os.getuid(), os.getgid()),
            ):
                with self.assertRaisesRegex(
                    entry.UpdateError, 'unsafe update-status file'
                ):
                    entry.atomic_json(status, {'state': 'new'})
            self.assertEqual(victim.read_text(encoding='utf-8'), 'victim\n')
            self.assertTrue(status.is_symlink())

    def test_installer_uses_entry_and_non_executable_implementation(self):
        installer = (TOOLS / 'install-update-agent.sh').read_text(
            encoding='utf-8'
        )
        self.assertIn(
            'UPDATER_ENTRY="$SOURCE_ROOT/tools/hostpanel-update-entry.py"',
            installer,
        )
        self.assertIn(
            'UPDATER_IMPL="$SOURCE_ROOT/tools/hostpanel-update.py"',
            installer,
        )
        self.assertIn(
            '"$UPDATER_IMPL" /opt/hostpanel/tools/hostpanel-update-impl.py',
            installer,
        )
        self.assertIn(
            '"$UPDATER_ENTRY" /opt/hostpanel/tools/hostpanel-update',
            installer,
        )
        self.assertIn('install -o root -g root -m 644', installer)
        self.assertIn('install -o root -g root -m 755', installer)

    def test_entry_overrides_only_status_writer(self):
        source = (TOOLS / 'hostpanel-update-entry.py').read_text(
            encoding='utf-8'
        )
        self.assertIn("_IMPL.atomic_json = atomic_json", source)
        self.assertIn("return _IMPL.main(argv)", source)
        self.assertNotIn('os.getpid()', source)
        self.assertIn('secrets.token_hex(12)', source)
        self.assertIn('os.fchmod(fd, 0o600)', source)
        self.assertIn('_fsync_parent(path)', source)


if __name__ == '__main__':
    unittest.main()
