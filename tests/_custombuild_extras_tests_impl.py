from __future__ import annotations

import importlib.util
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
sys.path.insert(0, str(TOOLS))

import hostpanel_build_config as CONFIG
import hostpanel_build_extras as EXTRAS


class CustomBuildExtrasTests(unittest.TestCase):
    def test_defaults_are_off(self):
        self.assertEqual(CONFIG.DEFAULT_OPTIONS['mongodb'], 'off')
        self.assertEqual(CONFIG.DEFAULT_OPTIONS['varnish'], 'off')
        self.assertEqual(CONFIG.validate_value('mongodb', '8.0'), '8.0')
        self.assertEqual(CONFIG.validate_value('varnish', 'on'), 'on')

    def test_component_packages(self):
        options = dict(CONFIG.DEFAULT_OPTIONS)
        platform = CONFIG.Platform('debian', 'ubuntu', '24.04')
        self.assertEqual(CONFIG.component_packages('mongodb', options, platform), [])
        self.assertEqual(CONFIG.component_packages('varnish', options, platform), [])
        options['mongodb'] = '8.0'
        options['varnish'] = 'on'
        self.assertEqual(CONFIG.component_packages('mongodb', options, platform), ['mongodb-org'])
        self.assertEqual(CONFIG.component_packages('varnish', options, platform), ['varnish'])

    def test_extra_components_are_role_aware(self):
        options = dict(CONFIG.DEFAULT_OPTIONS)
        options.update(mongodb='8.0', varnish='on')
        self.assertEqual(EXTRAS.extra_components(options, {'database'}), ['mongodb'])
        self.assertEqual(EXTRAS.extra_components(options, {'web'}), ['varnish'])
        self.assertEqual(
            EXTRAS.extra_components(options, {'web', 'database'}),
            ['mongodb', 'varnish'],
        )

    def test_mongodb_supported_platforms(self):
        self.assertEqual(
            EXTRAS.mongodb_supported(
                CONFIG.Platform('debian', 'ubuntu', '24.04'), 'x86_64'
            ),
            ('ubuntu', 'noble'),
        )
        self.assertEqual(
            EXTRAS.mongodb_supported(
                CONFIG.Platform('debian', 'debian', '12'), 'x86_64'
            ),
            ('debian', 'bookworm'),
        )
        self.assertEqual(
            EXTRAS.mongodb_supported(
                CONFIG.Platform('rhel', 'rocky', '9.4'), 'x86_64'
            ),
            ('rhel', '9'),
        )
        with self.assertRaises(CONFIG.BuildError):
            EXTRAS.mongodb_supported(
                CONFIG.Platform('debian', 'ubuntu', '26.04'), 'x86_64'
            )
        with self.assertRaises(CONFIG.BuildError):
            EXTRAS.mongodb_supported(
                CONFIG.Platform('debian', 'ubuntu', '24.04'), 'aarch64'
            )

    def test_mongodb_key_fingerprint_is_pinned(self):
        completed = subprocess.CompletedProcess(
            ['gpg'], 0,
            'pub:-:4096:1:41DE058A4E7DCA05:0:0:::::::\n'
            f'fpr:::::::::{EXTRAS.MONGODB_KEY_FINGERPRINT}:\n',
            '',
        )
        with mock.patch.object(EXTRAS, 'run_command', return_value=completed):
            EXTRAS.verify_mongodb_key(pathlib.Path('/tmp/key'))
        bad = subprocess.CompletedProcess(['gpg'], 0, 'fpr:::::::::DEADBEEF:\n', '')
        with mock.patch.object(EXTRAS, 'run_command', return_value=bad):
            with self.assertRaisesRegex(CONFIG.BuildError, 'fingerprint'):
                EXTRAS.verify_mongodb_key(pathlib.Path('/tmp/key'))

    def test_mongod_config_is_localhost_and_authorized(self):
        original = (
            'storage:\n'
            '  dbPath: /var/lib/mongodb\n'
            'net:\n'
            '  port: 27017\n'
            '  bindIp: 127.0.0.1\n'
        )
        updated = EXTRAS.harden_mongod_config(original)
        self.assertIn('bindIp: 127.0.0.1', updated)
        self.assertIn('security:\n  authorization: enabled', updated)
        self.assertEqual(updated, EXTRAS.harden_mongod_config(updated))
        exposed = original.replace('127.0.0.1', '0.0.0.0')
        with self.assertRaisesRegex(CONFIG.BuildError, 'non-loopback'):
            EXTRAS.harden_mongod_config(exposed)

    def test_mongodb_repo_contract_uses_https_and_pinned_key(self):
        source = (TOOLS / 'hostpanel_build_extras.py').read_text(encoding='utf-8')
        self.assertIn("MONGODB_KEY_FINGERPRINT = '4B0752C1BCA238C0B4EE14DC41DE058A4E7DCA05'", source)
        self.assertIn("'curl', '-q'", source)
        self.assertIn("'--proto', '=https'", source)
        self.assertIn('https://repo.mongodb.org/apt/ubuntu', source)
        self.assertIn('https://repo.mongodb.org/yum/redhat/', source)

    def test_varnish_mode_requires_separate_origin(self):
        options = dict(CONFIG.DEFAULT_OPTIONS)
        options['webserver'] = 'nginx_apache'
        self.assertEqual(EXTRAS.varnish_origin_port(options), 8080)
        options['webserver'] = 'apache'
        self.assertEqual(EXTRAS.varnish_origin_port(options), 8080)
        options['webserver'] = 'openlitespeed'
        self.assertEqual(EXTRAS.varnish_origin_port(options), 8088)
        options['webserver'] = 'nginx'
        with self.assertRaisesRegex(CONFIG.BuildError, 'requires'):
            EXTRAS.varnish_origin_port(options)

    def test_varnish_vcl_is_loopback_and_bypasses_private_requests(self):
        vcl = EXTRAS.render_varnish_vcl(8080)
        self.assertIn('.host = "127.0.0.1";', vcl)
        self.assertIn('.port = "8080";', vcl)
        self.assertIn('req.http.Authorization || req.http.Cookie', vcl)
        self.assertIn('beresp.http.Set-Cookie', vcl)
        self.assertIn('X-HostPanel-Cache', vcl)

    def test_varnish_dropin_binds_only_loopback(self):
        with mock.patch.object(EXTRAS, 'varnish_service_user', return_value='varnish'):
            dropin = EXTRAS.render_varnish_dropin('/usr/sbin/varnishd')
        self.assertIn('-a 127.0.0.1:6081', dropin)
        self.assertIn('-T 127.0.0.1:6082', dropin)
        self.assertIn('-j unix,user=varnish', dropin)
        self.assertNotIn('-a :6081', dropin)

    def test_rewrite_varnish_proxies_is_reversible(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            vhost = root / 'example.com'
            vhost.write_text(
                '# HostPanel Apache-only edge\n'
                'proxy_pass http://127.0.0.1:8080;\n',
                encoding='utf-8',
            )
            calls: list[list[str]] = []

            def command(argv, **kwargs):
                calls.append(argv)
                return subprocess.CompletedProcess(argv, 0, '', '')

            with mock.patch.object(EXTRAS, 'NGINX_AVAILABLE', root), \
                 mock.patch.object(EXTRAS, 'require_root'), \
                 mock.patch.object(EXTRAS, 'run_command', side_effect=command):
                EXTRAS.rewrite_varnish_proxies(True, 8080, root / 'log')
                enabled = vhost.read_text()
                self.assertIn('127.0.0.1:6081', enabled)
                self.assertIn('HostPanel Varnish origin 8080', enabled)
                EXTRAS.rewrite_varnish_proxies(False, 8080, root / 'log')
            disabled = vhost.read_text()
            self.assertIn('127.0.0.1:8080', disabled)
            self.assertNotIn('127.0.0.1:6081', disabled)
            self.assertNotIn('HostPanel Varnish origin', disabled)
            self.assertIn(['nginx', '-t'], calls)

    def test_varnish_runtime_patcher_is_idempotent(self):
        source = '''from pathlib import Path
MODES = ("nginx", "apache", "hybrid", "openlitespeed")

def apache_edge_tls_directives(domain, existing): return []
def apache_edge_vhost(domain: str, port: int, existing: str) -> str:
    directives = apache_edge_tls_directives(domain, existing)
    return str(port)

def render():
    if True:
            write_root_file(nginx_vhost, NGINX_PROXY_VHOST.format(
                domain=domain, docroot=docroot, port=APACHE_PORT))
            write_root_file(nginx_vhost, OLS_PROXY_VHOST.format(
                domain=domain, port=OLS_PORT))
'''
        spec = importlib.util.spec_from_file_location(
            'patch_varnish_runtime', TOOLS / 'patch_varnish_runtime.py'
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / 'webserver.py'
            path.write_text(source, encoding='utf-8')
            path.chmod(0o644)
            with mock.patch.object(module, 'trusted_file', side_effect=lambda p: p.stat()):
                module.patch(path)
                first = path.read_text(encoding='utf-8')
                module.patch(path)
            self.assertEqual(first, path.read_text(encoding='utf-8'))
            self.assertIn('backend_proxy_port(APACHE_PORT)', first)
            self.assertIn('backend_proxy_port(OLS_PORT)', first)
            compile(first, str(path), 'exec')

    def test_cli_rejects_varnish_with_nginx(self):
        source = (TOOLS / 'hostpanel_build_cli.py').read_text(encoding='utf-8')
        self.assertIn("options.get('varnish') == 'on'", source)
        self.assertIn("options.get('webserver') == 'nginx'", source)
        self.assertIn('apply_mongodb', source)
        self.assertIn('apply_varnish', source)

    def test_installer_defaults_and_compiles_extras(self):
        source = (TOOLS / 'install-hostpanel-build.sh').read_text(encoding='utf-8')
        subprocess.run(['bash', '-n', str(TOOLS / 'install-hostpanel-build.sh')], check=True)
        self.assertIn('hostpanel_build_extras.py', source)
        self.assertIn('patch_varnish_runtime.py', source)
        self.assertIn('mongodb=off', source)
        self.assertIn('varnish=off', source)
        self.assertIn('/opt/hostpanel/tools/patch-varnish-runtime', source)


if __name__ == '__main__':
    unittest.main()
