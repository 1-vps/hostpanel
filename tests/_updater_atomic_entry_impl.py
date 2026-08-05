from __future__ import annotations

import fcntl
import importlib.machinery
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
    loader = importlib.machinery.SourceFileLoader(name, str(entry))
    spec = importlib.util.spec_from_loader(name, loader)
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

    def test_implementation_write_adapter_completes_short_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = pathlib.Path(directory)
            entry = load_installed_entry(runtime)
            destination = runtime / 'payload.bin'
            descriptor = os.open(
                destination,
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                0o600,
            )
            real_write = os.write
            calls = 0

            def partial_write(fd: int, payload) -> int:
                nonlocal calls
                calls += 1
                view = memoryview(payload)
                return real_write(fd, view[:max(1, len(view) // 2)])

            try:
                with mock.patch.object(
                    entry, '_RAW_OS_WRITE', side_effect=partial_write
                ):
                    written = entry._IMPL.os.write(
                        descriptor, b'complete updater payload'
                    )
            finally:
                os.close(descriptor)

            self.assertEqual(written, len(b'complete updater payload'))
            self.assertGreater(calls, 1)
            self.assertEqual(
                destination.read_bytes(), b'complete updater payload'
            )

    def test_implementation_os_proxy_does_not_replace_process_os_write(self):
        with tempfile.TemporaryDirectory() as directory:
            original = os.write
            entry = load_installed_entry(pathlib.Path(directory))
            self.assertIs(os.write, original)
            self.assertIs(entry.os.write, original)
            self.assertIsNot(entry._IMPL.os, os)
            self.assertIs(entry._IMPL.os.write, entry._write_all)

    def test_failed_implementation_write_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            entry = load_installed_entry(pathlib.Path(directory))
            with mock.patch.object(entry, '_RAW_OS_WRITE', return_value=0):
                with self.assertRaisesRegex(
                    entry.UpdateError, 'could not complete updater file write'
                ):
                    entry._write_all(123, b'payload')

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

    def test_updater_lock_symlink_is_rejected_without_touching_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            runtime = root / 'runtime'
            runtime.mkdir(mode=0o700)
            entry = load_installed_entry(runtime)
            lock_dir = root / 'locks'
            lock_dir.mkdir(mode=0o700)
            victim = lock_dir / 'victim'
            victim.write_text('victim\n', encoding='utf-8')
            victim.chmod(0o600)
            lock = lock_dir / 'hostpanel-update.lock'
            lock.symlink_to(victim)

            with mock.patch.object(
                entry._IMPL, '_owner_ids',
                return_value=(os.getuid(), os.getgid()),
            ):
                with self.assertRaisesRegex(
                    entry.UpdateError, 'unsafe updater lock file'
                ):
                    with entry.safe_lock(lock):
                        self.fail('unsafe symlink lock was acquired')

            self.assertEqual(victim.read_text(encoding='utf-8'), 'victim\n')
            self.assertTrue(lock.is_symlink())

    def test_updater_lock_hardlink_and_wrong_mode_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            runtime = root / 'runtime'
            runtime.mkdir(mode=0o700)
            entry = load_installed_entry(runtime)
            lock_dir = root / 'locks'
            lock_dir.mkdir(mode=0o700)
            victim = lock_dir / 'victim'
            victim.write_text('victim\n', encoding='utf-8')
            victim.chmod(0o600)
            hardlink = lock_dir / 'hardlink.lock'
            os.link(victim, hardlink)
            wrong_mode = lock_dir / 'wrong-mode.lock'
            wrong_mode.write_text('', encoding='utf-8')
            wrong_mode.chmod(0o644)

            with mock.patch.object(
                entry._IMPL, '_owner_ids',
                return_value=(os.getuid(), os.getgid()),
            ):
                for lock in (hardlink, wrong_mode):
                    with self.subTest(lock=lock), self.assertRaisesRegex(
                        entry.UpdateError, 'unsafe updater lock file'
                    ):
                        with entry.safe_lock(lock):
                            self.fail('unsafe lock was acquired')

    def test_competing_updater_lock_returns_temporary_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            runtime = root / 'runtime'
            runtime.mkdir(mode=0o700)
            entry = load_installed_entry(runtime)
            lock_dir = root / 'locks'
            lock_dir.mkdir(mode=0o700)
            lock = lock_dir / 'hostpanel-update.lock'
            lock.write_bytes(b'')
            lock.chmod(0o600)
            descriptor = os.open(lock, os.O_RDWR)
            try:
                fcntl.flock(
                    descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB
                )
                with mock.patch.object(
                    entry._IMPL, '_owner_ids',
                    return_value=(os.getuid(), os.getgid()),
                ), mock.patch.object(entry._IMPL, 'run') as run:
                    result = entry.main([
                        '--allow-non-root',
                        '--lock-file', str(lock),
                        '--status-file', str(root / 'status.json'),
                    ])
            finally:
                os.close(descriptor)

            self.assertEqual(result, 75)
            run.assert_not_called()

    def test_safe_lock_creates_private_single_link_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            runtime = root / 'runtime'
            runtime.mkdir(mode=0o700)
            entry = load_installed_entry(runtime)
            lock_dir = root / 'locks'
            lock_dir.mkdir(mode=0o700)
            lock = lock_dir / 'hostpanel-update.lock'

            with mock.patch.object(
                entry._IMPL, '_owner_ids',
                return_value=(os.getuid(), os.getgid()),
            ):
                with entry.safe_lock(lock):
                    metadata = lock.lstat()
                    self.assertTrue(stat.S_ISREG(metadata.st_mode))
                    self.assertEqual(metadata.st_nlink, 1)
                    self.assertEqual(
                        stat.S_IMODE(metadata.st_mode), 0o600
                    )

    def test_installer_embeds_entry_and_non_executable_implementation(self):
        installer = (TOOLS / 'install-update-agent.sh').read_text(
            encoding='utf-8'
        )
        source = (TOOLS / 'hostpanel-update-entry.py').read_text(
            encoding='utf-8'
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
            "python3 - /opt/hostpanel/tools/hostpanel-update <<'PYENTRY'",
            installer,
        )
        self.assertIn("ENTRY_PAYLOAD = r'''" + source + "'''", installer)
        self.assertNotIn('UPDATER_ENTRY=', installer)
        self.assertIn('os.replace(temporary, destination)', installer)
        self.assertIn('os.fsync(directory_fd)', installer)
        self.assertIn('install -o root -g root -m 644', installer)

    def test_entry_overrides_status_writer_and_implementation_write_boundary(self):
        source = (TOOLS / 'hostpanel-update-entry.py').read_text(
            encoding='utf-8'
        )
        self.assertIn("_IMPL.atomic_json = atomic_json", source)
        self.assertIn("_IMPL.os = _UPDATER_OS", source)
        self.assertIn("return _IMPL.run(args)", source)
        self.assertNotIn('os.getpid()', source)
        self.assertIn('secrets.token_hex(12)', source)
        self.assertIn('os.fchmod(fd, 0o600)', source)
        self.assertIn('_fsync_parent(path)', source)
        self.assertIn('with safe_lock(pathlib.Path(args.lock_file))', source)
        self.assertIn('os.O_NOFOLLOW', source)
        self.assertNotIn('return _IMPL.main(argv)', source)


if __name__ == '__main__':
    unittest.main()
