from __future__ import annotations

import io
import pathlib
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
sys.path.insert(0, str(TOOLS))

import hostpanel_build_ssl as SSL


class CustomBuildSslTests(unittest.TestCase):
    def test_domain_email_and_provider_validation(self):
        self.assertEqual(SSL.validate_domain('Example.COM.'), 'example.com')
        self.assertEqual(SSL.validate_email('admin@example.com'), 'admin@example.com')
        self.assertEqual(SSL.validate_provider('ZeroSSL'), 'zerossl')
        for value in ('localhost', '../example.com', '192.0.2.1', '*.example.com'):
            with self.assertRaises(SSL.BuildError):
                SSL.validate_domain(value)
        for value in ('missing-at', 'a b@example.com', '@example.com'):
            with self.assertRaises(SSL.BuildError):
                SSL.validate_email(value)
        with self.assertRaises(SSL.BuildError):
            SSL.validate_provider('unknown-ca')

    def test_plan_does_not_run_commands(self):
        output = io.StringIO()
        with mock.patch.object(SSL, 'run_command') as run, redirect_stdout(output):
            SSL.print_ssl_plan(
                'issue', 'example.com', 'admin@example.com', True, 'zerossl'
            )
        run.assert_not_called()
        self.assertIn('provider: zerossl', output.getvalue())
        self.assertIn('root-protected EAB credentials', output.getvalue())
        self.assertIn('No changes are made without --apply.', output.getvalue())

    def _certificate_tree(self, root: pathlib.Path):
        available = root / 'available'
        enabled = root / 'enabled'
        live = root / 'live'
        hook = root / 'hooks' / 'hostpanel-reload-nginx'
        available.mkdir()
        enabled.mkdir()
        vhost = available / 'example.com'
        vhost.write_text('server { listen 80; }\n', encoding='utf-8')
        (enabled / 'example.com').symlink_to(vhost)
        lineage = live / 'example.com'
        lineage.mkdir(parents=True)
        (lineage / 'fullchain.pem').write_text('certificate')
        (lineage / 'privkey.pem').write_text('key')
        return available, enabled, live, hook

    def test_letsencrypt_issue_uses_nginx_plugin_and_installs_hook(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            available, enabled, live, hook = self._certificate_tree(root)
            commands: list[list[str]] = []
            with mock.patch.object(SSL, 'ensure_certbot', return_value='/usr/bin/certbot'), \
                 mock.patch.object(SSL, 'run_command', side_effect=lambda command, **kwargs: commands.append(command) or subprocess.CompletedProcess(command, 0)), \
                 mock.patch.object(SSL, 'install_deploy_hook') as deploy:
                SSL.issue_certificate(
                    'example.com', 'admin@example.com', True, root / 'log',
                    available_root=available, enabled_root=enabled,
                    live_root=live, hook_path=hook,
                )
            certbot = commands[1]
            self.assertEqual(certbot[0:2], ['/usr/bin/certbot', '--nginx'])
            self.assertNotIn('--config', certbot)
            self.assertIn('--redirect', certbot)
            self.assertIn('www.example.com', certbot)
            deploy.assert_called_once_with(hook)
            self.assertEqual(commands[-1], ['systemctl', 'reload', 'nginx.service'])

    def test_zerossl_issue_uses_private_config_not_secret_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            available, enabled, live, hook = self._certificate_tree(root)
            kid_file = root / 'kid'
            hmac_file = root / 'hmac'
            kid = 'KID_value_123456'
            hmac = 'HMAC_value_1234567890'
            kid_file.write_text(kid + '\n', encoding='ascii')
            hmac_file.write_text(hmac + '\n', encoding='ascii')
            kid_file.chmod(0o600)
            hmac_file.chmod(0o600)
            commands: list[list[str]] = []
            config_payloads: list[str] = []

            def capture(command, **kwargs):
                commands.append(command)
                if '--config' in command:
                    config = pathlib.Path(command[command.index('--config') + 1])
                    config_payloads.append(config.read_text(encoding='ascii'))
                return subprocess.CompletedProcess(command, 0)

            with mock.patch.object(SSL, 'ensure_certbot', return_value='/usr/bin/certbot'), \
                 mock.patch.object(SSL, 'run_command', side_effect=capture), \
                 mock.patch.object(SSL, 'install_deploy_hook'):
                SSL.issue_certificate(
                    'example.com', 'admin@example.com', False, root / 'log',
                    provider='zerossl', eab_kid_file=kid_file,
                    eab_hmac_file=hmac_file, available_root=available,
                    enabled_root=enabled, live_root=live, hook_path=hook,
                    runtime_dir=root / 'runtime',
                )
            certbot = commands[1]
            self.assertIn('--config', certbot)
            self.assertNotIn(kid, certbot)
            self.assertNotIn(hmac, certbot)
            self.assertEqual(len(config_payloads), 1)
            self.assertIn(SSL.ZEROSSL_SERVER, config_payloads[0])
            self.assertIn(f'eab-kid = {kid}', config_payloads[0])
            self.assertIn(f'eab-hmac-key = {hmac}', config_payloads[0])
            self.assertFalse(any((root / 'runtime').glob('zerossl-*.ini')))

    def test_eab_files_must_be_private(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / 'kid'
            path.write_text('KID_value_123456\n', encoding='ascii')
            path.chmod(0o644)
            with self.assertRaisesRegex(SSL.BuildError, 'unsafe ZeroSSL EAB KID'):
                SSL.read_eab_secret(path, 'ZeroSSL EAB KID')
            path.chmod(0o600)
            self.assertEqual(
                SSL.read_eab_secret(path, 'ZeroSSL EAB KID'),
                'KID_value_123456',
            )

    def test_deploy_hook_is_private_and_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            hook = pathlib.Path(directory) / 'deploy' / 'hostpanel-reload-nginx'
            SSL.install_deploy_hook(hook)
            first = hook.read_text(encoding='utf-8')
            with mock.patch.object(SSL, '_open_hook_temporary') as opened:
                SSL.install_deploy_hook(hook)
            opened.assert_not_called()
            self.assertEqual(first, hook.read_text(encoding='utf-8'))
            self.assertEqual(hook.stat().st_mode & 0o777, 0o755)
            self.assertIn('nginx -t', first)
            self.assertIn('systemctl reload nginx.service', first)

    def test_deploy_hook_preserves_xattrs_and_fsyncs_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            hook = pathlib.Path(directory) / 'deploy' / 'hostpanel-reload-nginx'
            hook.parent.mkdir()
            hook.write_text('#!/bin/sh\nexit 1\n', encoding='utf-8')
            hook.chmod(0o755)
            applied: list[tuple[pathlib.Path, dict[str, bytes]]] = []
            with mock.patch.object(
                SSL, '_capture_xattrs',
                return_value={'security.selinux': b'certbot-hook'},
            ), mock.patch.object(
                SSL, '_apply_xattrs',
                side_effect=lambda path, values: applied.append(
                    (path, dict(values))
                ),
            ), mock.patch.object(SSL.os, 'fchown'), \
                 mock.patch.object(SSL, '_fsync_parent') as fsync_parent:
                SSL.install_deploy_hook(hook)
            self.assertEqual(hook.read_text(encoding='utf-8'), SSL.HOOK_TEXT)
            self.assertEqual(hook.stat().st_mode & 0o777, 0o755)
            self.assertEqual(len(applied), 1)
            self.assertNotEqual(applied[0][0], hook)
            self.assertEqual(
                applied[0][1], {'security.selinux': b'certbot-hook'}
            )
            fsync_parent.assert_called_once_with(hook)

    def test_deploy_hook_write_failure_preserves_old_file_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as directory:
            hook = pathlib.Path(directory) / 'deploy' / 'hostpanel-reload-nginx'
            hook.parent.mkdir()
            original = '#!/bin/sh\nexit 1\n'
            hook.write_text(original, encoding='utf-8')
            hook.chmod(0o755)
            with mock.patch.object(
                SSL, '_capture_xattrs', return_value={}
            ), mock.patch.object(
                SSL.secrets, 'token_hex', return_value='fresh'
            ), mock.patch.object(SSL.os, 'write', return_value=0):
                with self.assertRaisesRegex(
                    SSL.BuildError, 'could not write Certbot deploy hook'
                ):
                    SSL.install_deploy_hook(hook)
            self.assertEqual(hook.read_text(encoding='utf-8'), original)
            self.assertFalse(
                (hook.parent / '.hostpanel-reload-nginx.hostpanel-ssl.fresh').exists()
            )

    def test_status_uses_cert_name_filter(self):
        completed = subprocess.CompletedProcess([], 0, stdout='Certificate Name: example.com\n', stderr='')
        output = io.StringIO()
        with mock.patch.object(SSL, 'ensure_certbot', return_value='/usr/bin/certbot'), \
             mock.patch.object(SSL, 'run_command', return_value=completed) as run, \
             redirect_stdout(output):
            rc = SSL.certificate_status('example.com')
        self.assertEqual(rc, 0)
        run.assert_called_once_with(
            ['/usr/bin/certbot', 'certificates', '--cert-name', 'example.com'],
            check=False, capture=True,
        )
        self.assertIn('Certificate Name', output.getvalue())


if __name__ == '__main__':
    unittest.main()
