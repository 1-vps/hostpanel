from __future__ import annotations

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

import hostpanel_build_ssl as ssl


class SslHookModeAtomicityTests(unittest.TestCase):
    def test_matching_hook_with_wrong_mode_uses_atomic_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            hook = pathlib.Path(directory) / 'deploy' / 'hostpanel-reload-nginx'
            hook.parent.mkdir()
            hook.write_text(ssl.HOOK_TEXT, encoding='utf-8')
            hook.chmod(0o700)
            applied: list[tuple[pathlib.Path, dict[str, bytes]]] = []

            with mock.patch.object(
                ssl, '_capture_xattrs',
                return_value={'security.selinux': b'certbot-hook'},
            ), mock.patch.object(
                ssl, '_apply_xattrs',
                side_effect=lambda path, values: applied.append(
                    (path, dict(values))
                ),
            ), mock.patch.object(ssl.os, 'fchown'), mock.patch.object(
                ssl, '_enforce_existing_hook_mode'
            ) as legacy_mode, mock.patch.object(
                ssl, '_fsync_parent'
            ) as fsync_parent:
                ssl.install_deploy_hook(hook)

            legacy_mode.assert_not_called()
            self.assertEqual(hook.read_text(encoding='utf-8'), ssl.HOOK_TEXT)
            self.assertEqual(stat.S_IMODE(hook.stat().st_mode), 0o755)
            self.assertEqual(len(applied), 1)
            self.assertNotEqual(applied[0][0], hook)
            self.assertEqual(
                applied[0][1], {'security.selinux': b'certbot-hook'}
            )
            fsync_parent.assert_called_once_with(hook)

    def test_matching_hook_with_correct_mode_remains_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            hook = pathlib.Path(directory) / 'deploy' / 'hostpanel-reload-nginx'
            hook.parent.mkdir()
            hook.write_text(ssl.HOOK_TEXT, encoding='utf-8')
            hook.chmod(0o755)
            with mock.patch.object(ssl, '_open_hook_temporary') as opened, \
                 mock.patch.object(ssl, '_capture_xattrs') as capture:
                ssl.install_deploy_hook(hook)
            opened.assert_not_called()
            capture.assert_not_called()


if __name__ == '__main__':
    unittest.main()
