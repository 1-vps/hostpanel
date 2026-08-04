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

import hostpanel_build_ssl as ssl


class ZeroSslCleanupTests(unittest.TestCase):
    def test_close_failure_still_unlinks_secret_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = pathlib.Path(directory)
            secret = runtime / 'zerossl-test.ini'
            descriptor = os.open(
                secret,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            try:
                with mock.patch.object(
                    ssl, 'read_eab_secret', side_effect=['kid-value', 'hmac-value']
                ), mock.patch.object(ssl, '_safe_root_directory'), \
                     mock.patch.object(
                         ssl.tempfile, 'mkstemp',
                         return_value=(descriptor, str(secret)),
                     ), mock.patch.object(ssl.os, 'fchown'), \
                     mock.patch.object(
                         ssl.os, 'close', side_effect=OSError('close failed')
                     ):
                    with self.assertRaisesRegex(OSError, 'close failed'):
                        with ssl.zerossl_certbot_config(
                            pathlib.Path('/kid'),
                            pathlib.Path('/hmac'),
                            runtime,
                        ):
                            self.fail('context should not be entered after close failure')
                self.assertFalse(secret.exists())
            finally:
                try:
                    os.close(descriptor)
                except OSError:
                    pass

    def test_body_error_is_not_replaced_by_cleanup_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = pathlib.Path(directory)
            with mock.patch.object(
                ssl, 'read_eab_secret', side_effect=['kid-value', 'hmac-value']
            ), mock.patch.object(ssl.os, 'fchown'):
                with self.assertRaisesRegex(RuntimeError, 'certbot failed'):
                    with ssl.zerossl_certbot_config(
                        pathlib.Path('/kid'),
                        pathlib.Path('/hmac'),
                        runtime,
                    ):
                        raise RuntimeError('certbot failed')
            self.assertEqual(list(runtime.glob('zerossl-*.ini')), [])


if __name__ == '__main__':
    unittest.main()
