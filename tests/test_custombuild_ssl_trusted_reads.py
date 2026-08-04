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
import hostpanel_build_ssl as ssl
from hostpanel_build_config import BuildError


class SslTrustedReadTests(unittest.TestCase):
    def test_new_hook_uses_final_path_selinux_context(self) -> None:
        events: list[str] = []
        with tempfile.TemporaryDirectory() as directory:
            hook = pathlib.Path(directory) / 'deploy' / 'hostpanel-reload-nginx'
            hook.parent.mkdir()

            def apply_context(fd: int, path: pathlib.Path, mode: int) -> None:
                self.assertGreaterEqual(fd, 0)
                self.assertEqual(path, hook)
                self.assertEqual(mode, 0o755)
                events.append('selinux')

            real_replace = os.replace

            def replace(source, destination) -> None:
                events.append('replace')
                real_replace(source, destination)

            with mock.patch.object(ssl.os, 'fchown'), mock.patch.object(
                ssl, '_apply_expected_selinux_context',
                side_effect=apply_context,
            ) as selinux, mock.patch.object(
                ssl, '_apply_xattrs'
            ) as xattrs, mock.patch.object(
                ssl.os, 'replace', side_effect=replace
            ), mock.patch.object(ssl, '_fsync_parent'):
                ssl.install_deploy_hook(hook)

            selinux.assert_called_once()
            xattrs.assert_not_called()
            self.assertLess(events.index('selinux'), events.index('replace'))
            self.assertEqual(hook.read_text(encoding='utf-8'), ssl.HOOK_TEXT)

    def test_eab_secret_path_swap_during_read_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            secret = pathlib.Path(directory) / 'kid'
            secret.write_text('original-kid-value\n', encoding='ascii')
            secret.chmod(0o600)
            real_read = os.read
            swapped = False

            def read_then_swap(fd: int, size: int) -> bytes:
                nonlocal swapped
                payload = real_read(fd, size)
                if payload and not swapped:
                    swapped = True
                    secret.unlink()
                    secret.write_text('replacement-kid\n', encoding='ascii')
                    secret.chmod(0o600)
                return payload

            with mock.patch.object(
                ssl, 'owner_ids', return_value=(os.getuid(), os.getgid())
            ), mock.patch.object(config.os, 'read', side_effect=read_then_swap):
                with self.assertRaisesRegex(
                    BuildError, 'ZeroSSL EAB KID changed during read'
                ):
                    ssl.read_eab_secret(secret, 'ZeroSSL EAB KID')

    def test_stable_eab_secret_is_returned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            secret = pathlib.Path(directory) / 'hmac'
            secret.write_text('stable-hmac-value\n', encoding='ascii')
            secret.chmod(0o600)
            with mock.patch.object(
                ssl, 'owner_ids', return_value=(os.getuid(), os.getgid())
            ):
                self.assertEqual(
                    ssl.read_eab_secret(secret, 'ZeroSSL EAB HMAC'),
                    'stable-hmac-value',
                )


if __name__ == '__main__':
    unittest.main()
