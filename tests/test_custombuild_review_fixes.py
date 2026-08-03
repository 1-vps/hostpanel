from __future__ import annotations

import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
sys.path.insert(0, str(TOOLS))

import hostpanel_build_config as CONFIG
import hostpanel_build_operations as OPERATIONS
import patch_custombuild_runtime as PATCH


class CustomBuildReviewFixTests(unittest.TestCase):
    def test_dns_packages_include_validation_utilities(self):
        options = dict(CONFIG.DEFAULT_OPTIONS)
        self.assertEqual(
            CONFIG.component_packages('dns', options, CONFIG.Platform('debian', 'ubuntu', '24.04')),
            ['bind9', 'bind9-utils'],
        )
        self.assertEqual(
            CONFIG.component_packages('dns', options, CONFIG.Platform('rhel', 'rocky', '9')),
            ['bind', 'bind-utils'],
        )

    def test_dns_service_matches_platform(self):
        options = dict(CONFIG.DEFAULT_OPTIONS)
        self.assertEqual(
            OPERATIONS.services_for('dns', options, CONFIG.Platform('debian', 'debian', '13')),
            ['bind9'],
        )
        self.assertEqual(
            OPERATIONS.services_for('dns', options, CONFIG.Platform('rhel', 'rocky', '9')),
            ['named'],
        )

    def test_preflight_requires_candidate_even_when_package_is_installed(self):
        options = dict(CONFIG.DEFAULT_OPTIONS)
        platform = CONFIG.Platform('debian', 'ubuntu', '24.04')
        with mock.patch.object(OPERATIONS, 'candidate_version', return_value=None):
            with self.assertRaisesRegex(CONFIG.BuildError, 'no services were changed'):
                OPERATIONS.preflight_packages(['nginx'], options, platform)

    def test_apache_edge_preserves_certbot_tls_and_redirect(self):
        namespace = {'Path': pathlib.Path}
        exec(PATCH.APACHE_EDGE_SUPPORT, namespace)
        existing = '''server {
    listen 80;
    server_name example.com www.example.com;
    return 301 https://$host$request_uri;
}
server {
    listen 443 ssl; # managed by Certbot
    ssl_certificate /etc/letsencrypt/live/example.com/fullchain.pem; # managed by Certbot
    ssl_certificate_key /etc/letsencrypt/live/example.com/privkey.pem; # managed by Certbot
    include /etc/letsencrypt/options-ssl-nginx.conf; # managed by Certbot
}
'''
        rendered = namespace['apache_edge_vhost']('example.com', 8080, existing)
        self.assertIn('listen 443 ssl;', rendered)
        self.assertIn('return 301 https://$host$request_uri;', rendered)
        self.assertIn('ssl_certificate /etc/letsencrypt/live/example.com/fullchain.pem;', rendered)
        self.assertIn('ssl_certificate_key /etc/letsencrypt/live/example.com/privkey.pem;', rendered)
        self.assertIn('proxy_pass http://127.0.0.1:8080;', rendered)

    def test_apache_edge_does_not_invent_redirect_for_existing_nonredirect_tls(self):
        namespace = {'Path': pathlib.Path}
        exec(PATCH.APACHE_EDGE_SUPPORT, namespace)
        existing = '''server {
    listen 443 ssl;
    ssl_certificate /etc/letsencrypt/live/example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/example.com/privkey.pem;
}
'''
        rendered = namespace['apache_edge_vhost']('example.com', 8080, existing)
        self.assertNotIn('return 301 https://$host$request_uri;', rendered)
        self.assertIn('proxy_pass http://127.0.0.1:8080;', rendered)

    def test_apache_support_migrates_pristine_and_v1_shapes_idempotently(self):
        anchor = 'OLS_PROXY_VHOST = """server {{\n'
        for original in (
            anchor,
            PATCH.APACHE_EDGE_SUPPORT_V1 + '\n' + anchor,
        ):
            migrated = PATCH.install_apache_edge_support(original, anchor)
            self.assertEqual(migrated.count('APACHE_EDGE_TLS_VHOST = """'), 1)
            self.assertEqual(migrated.count('APACHE_EDGE_REDIRECT_VHOST = """'), 1)
            self.assertEqual(migrated.count(anchor), 1)
            self.assertEqual(
                PATCH.install_apache_edge_support(migrated, anchor), migrated
            )

    def test_reviewed_variant_replacement_is_idempotent(self):
        first = 'old-a\n'
        second = 'old-b\n'
        new = 'new\n'
        self.assertEqual(
            PATCH.replace_variant_once(first, (first, second), new, 'test'), new
        )
        self.assertEqual(
            PATCH.replace_variant_once(new, (first, second), new, 'test'), new
        )
        with self.assertRaises(SystemExit):
            PATCH.replace_variant_once(first + second, (first, second), new, 'test')


if __name__ == '__main__':
    unittest.main()
