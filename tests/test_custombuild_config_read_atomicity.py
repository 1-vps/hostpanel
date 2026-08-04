from __future__ import annotations

import os
import pathlib
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


class CustomBuildConfigReadAtomicityTests(unittest.TestCase):
    def write_config(self, path: pathlib.Path, dns: str = 'bind') -> None:
        values = dict(config.DEFAULT_OPTIONS)
        values['dns'] = dns
        path.write_text(config.render_config(values), encoding='utf-8')
        path.chmod(0o600)

    def test_path_swap_before_open_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            target = root / 'build.conf'
            victim = root / 'victim.conf'
            self.write_config(target, 'bind')
            self.write_config(victim, 'powerdns')
            real_open = os.open
            swapped = False

            def swap_then_open(path, flags, *args):
                nonlocal swapped
                if pathlib.Path(path) == target and not swapped:
                    swapped = True
                    target.unlink()
                    target.symlink_to(victim)
                return real_open(path, flags, *args)

            with mock.patch.object(
                config, 'owner_ids', return_value=(os.getuid(), os.getgid())
            ), mock.patch.object(config.os, 'open', side_effect=swap_then_open):
                with self.assertRaisesRegex(
                    BuildError, 'cannot open build configuration'
                ):
                    config.read_config(target)
            self.assertTrue(target.is_symlink())
            self.assertEqual(config.parse_config_text(victim.read_text())['dns'], 'powerdns')

    def test_path_swap_during_read_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            target = root / 'build.conf'
            self.write_config(target, 'bind')
            real_read = os.read
            swapped = False

            def read_then_swap(fd: int, size: int) -> bytes:
                nonlocal swapped
                payload = real_read(fd, size)
                if payload and not swapped:
                    swapped = True
                    target.unlink()
                    self.write_config(target, 'powerdns')
                return payload

            with mock.patch.object(
                config, 'owner_ids', return_value=(os.getuid(), os.getgid())
            ), mock.patch.object(config.os, 'read', side_effect=read_then_swap):
                with self.assertRaisesRegex(
                    BuildError, 'build configuration changed during read'
                ):
                    config.read_config(target)
            self.assertEqual(config.read_config(target)['dns'], 'powerdns')

    def test_roles_path_swap_during_read_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            roles = root / 'roles.conf'
            roles.write_text('roles=web dns\n', encoding='utf-8')
            roles.chmod(0o600)
            real_read = os.read
            swapped = False

            def read_then_swap(fd: int, size: int) -> bytes:
                nonlocal swapped
                payload = real_read(fd, size)
                if payload and not swapped:
                    swapped = True
                    roles.unlink()
                    roles.write_text('roles=database\n', encoding='utf-8')
                    roles.chmod(0o600)
                return payload

            with mock.patch.object(
                config, 'owner_ids', return_value=(os.getuid(), os.getgid())
            ), mock.patch.object(config.os, 'read', side_effect=read_then_swap):
                with self.assertRaisesRegex(
                    BuildError, 'HostPanel role configuration changed during read'
                ):
                    config.read_roles(roles)


if __name__ == '__main__':
    unittest.main()
