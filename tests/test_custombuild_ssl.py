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
    def test_domain_and_email_validation(self):
        self.assertEqual(SSL.validate_domain('Example.COM.'), 'example.com')
        self.assertEqual(SSL.validate_email('admin@example.com'), 'admin@example.com')
        for value in ('localhost', '../example.com', '192.0.2.1', '*.example.com'):
            with self.assertRaises(SSL.BuildError):
                SSL.validate_domain(value)
        for value in ('missing-at', 'a b@example.com', '@example.com'):
            with self.assertRaises(SSL.BuildError):
                SSL.validate_email(value)

    def test_plan_does_not_run_commands(self):
        output = io.StringIO()
        with mock.patch.object(SSL, 'run_command') as run, redirect_stdout(output):
            SSL.print_ssl_plan('issue', 'example.com', 'admin@example.com', True)
        run.assert_not_called()
        self.assertIn("Let's Encrypt", output.getvalue())
        self.assertIn('No changes are made without --apply.', output.getvalue())

    def test_issue_uses_nginx_plugin_and_installs_hook(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
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
            self.assertIn('--redirect', certbot)
            self.assertIn('--cert-name', certbot)
            self.assertIn('www.example.com', certbot)
            deploy.assert_called_once_with(hook)
            self.assertEqual(commands[-1], ['systemctl', 'reload', 'nginx.service'])

    def test_deploy_hook_is_private_and_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            hook = pathlib.Path(directory) / 'deploy' / 'hostpanel-reload-nginx'
            SSL.install_deploy_hook(hook)
            first = hook.read_text(encoding='utf-8')
            SSL.install_deploy_hook(hook)
            self.assertEqual(first, hook.read_text(encoding='utf-8'))
            self.assertEqual(hook.stat().st_mode & 0o777, 0o755)
            self.assertIn('nginx -t', first)
            self.assertIn('systemctl reload nginx.service', first)

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
