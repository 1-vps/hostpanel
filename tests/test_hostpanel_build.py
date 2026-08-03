from __future__ import annotations

import io
import json
import pathlib
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'tools' / 'hostpanel-build.py'
INSTALLER = ROOT / 'tools' / 'install-hostpanel-build.sh'
PATCHER = ROOT / 'tools' / 'patch_custombuild_runtime.py'
DOC = ROOT / 'CUSTOMBUILD.md'
sys.path.insert(0, str(ROOT / 'tools'))
import hostpanel_build_cli as CLI
import hostpanel_build_config as CONFIG
import hostpanel_build_packages as PACKAGES
import hostpanel_build_operations as OPERATIONS
import patch_custombuild_runtime as PATCH


class HostPanelBuildTests(unittest.TestCase):
    def test_syntax_and_help(self):
        paths = [
            SCRIPT,
            ROOT / 'tools' / 'hostpanel_build_config.py',
            ROOT / 'tools' / 'hostpanel_build_packages.py',
            ROOT / 'tools' / 'hostpanel_build_operations.py',
            ROOT / 'tools' / 'hostpanel_build_cli.py',
            ROOT / 'tools' / 'hostpanel_build_ssl.py',
            ROOT / 'tools' / 'hostpanel_build_web.py',
            PATCHER,
        ]
        subprocess.run(['python3', '-m', 'py_compile', *map(str, paths)], check=True)
        subprocess.run(['bash', '-n', str(INSTALLER)], check=True)
        result = subprocess.run(
            ['python3', str(SCRIPT), '--help'], check=True,
            capture_output=True, text=True,
        )
        self.assertIn('CustomBuild-style', result.stdout)
        self.assertIn('update_versions', result.stdout)
        self.assertIn('update_panel', result.stdout)
        self.assertIn('ssl', result.stdout)

    def test_installer_and_documentation_contract(self):
        installer = INSTALLER.read_text(encoding='utf-8')
        document = DOC.read_text(encoding='utf-8')
        self.assertIn('/usr/local/sbin/hostpanel-build', installer)
        self.assertIn('/opt/hostpanel/tools/hostpanel-build-web', installer)
        self.assertIn('/opt/hostpanel/tools/patch-custombuild-runtime', installer)
        self.assertIn('hostpanel_build_ssl.py', installer)
        self.assertIn('/etc/hostpanel/ssl', installer)
        self.assertIn('webserver=nginx_apache', installer)
        self.assertIn('PHP_STATE=/etc/hostpanel/php-versions', installer)
        self.assertIn('chmod 0600 "$CONFIG"', installer)
        self.assertIn('Base mode: nginx_apache', installer)
        self.assertIn('set webserver openlitespeed', document)
        self.assertIn('build web --apply', document)
        self.assertIn('base HostPanel installation always starts in `nginx_apache`', document)
        self.assertIn('runtime-masked', document)
        self.assertIn('reverse order', document)
        self.assertIn('--provider letsencrypt', document)
        self.assertIn('--provider zerossl', document)
        self.assertIn('zerossl-eab-kid', document)

    def test_config_parsing_is_strict(self):
        values = CONFIG.parse_config_text(
            'webserver=openlitespeed\nphp_versions=8.5,8.4\n'
        )
        self.assertEqual(values['webserver'], 'openlitespeed')
        self.assertEqual(values['php_versions'], '8.5,8.4')
        for value in ('nginx_apache', 'nginx', 'apache', 'openlitespeed'):
            self.assertEqual(CONFIG.validate_value('webserver', value), value)
        with self.assertRaises(CONFIG.BuildError):
            CONFIG.parse_config_text('webserver=nginx\nwebserver=apache\n')
        with self.assertRaises(CONFIG.BuildError):
            CONFIG.parse_config_text('webserver=hybrid\n')
        with self.assertRaises(CONFIG.BuildError):
            CONFIG.parse_config_text('unknown=yes\n')
        with self.assertRaises(CONFIG.BuildError):
            CONFIG.parse_config_text('php_versions=8.5,8.5\n')

    def test_config_write_is_atomic_and_private(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / 'build.conf'
            options = dict(CONFIG.DEFAULT_OPTIONS)
            options['mta'] = 'exim'
            CONFIG.write_config(path, options)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(CONFIG.read_config(path)['mta'], 'exim')
            self.assertEqual(list(path.parent.glob('.build.conf.*')), [])

    def test_webserver_component_mapping(self):
        options = dict(CONFIG.DEFAULT_OPTIONS)
        self.assertEqual(CONFIG.web_components(options), ['nginx', 'apache', 'php'])
        self.assertEqual(CONFIG.panel_web_mode(options), 'hybrid')
        options['webserver'] = 'nginx'
        self.assertEqual(CONFIG.web_components(options), ['nginx', 'php'])
        options['webserver'] = 'apache'
        self.assertEqual(CONFIG.web_components(options), ['nginx', 'apache', 'php'])
        options['webserver'] = 'openlitespeed'
        self.assertEqual(CONFIG.web_components(options), ['nginx', 'openlitespeed', 'php'])

    def test_component_mapping_debian_and_rhel(self):
        options = dict(CONFIG.DEFAULT_OPTIONS)
        debian = CONFIG.Platform('debian', 'ubuntu', '24.04')
        rhel = CONFIG.Platform('rhel', 'rocky', '9')
        self.assertEqual(CONFIG.component_packages('apache', options, debian), ['apache2'])
        self.assertEqual(CONFIG.component_packages('apache', options, rhel), ['httpd'])
        self.assertIn('php8.5-fpm', CONFIG.component_packages('php', options, debian))
        self.assertIn('php85-php-fpm', CONFIG.component_packages('php', options, rhel))
        self.assertEqual(
            CONFIG.component_packages('dns', options, debian),
            ['bind9', 'bind9-utils'],
        )
        self.assertEqual(
            CONFIG.component_packages('dns', options, rhel),
            ['bind', 'bind-utils'],
        )
        options['php_versions'] = '8.5'
        self.assertIn(
            'lsphp85-mysql',
            CONFIG.component_packages('openlitespeed', options, debian),
        )
        self.assertIn(
            'lsphp85-mysqlnd',
            CONFIG.component_packages('openlitespeed', options, rhel),
        )

    def test_plan_is_non_mutating(self):
        options = dict(CONFIG.DEFAULT_OPTIONS)
        platform = CONFIG.Platform('debian', 'ubuntu', '24.04')
        with mock.patch.object(PACKAGES, 'run_command') as run:
            output = io.StringIO()
            with redirect_stdout(output):
                CLI.print_plan('web', options, platform)
            run.assert_not_called()
        self.assertIn('No changes are made without --apply.', output.getvalue())
        self.assertIn('nginx', output.getvalue())
        self.assertIn('apache2', output.getvalue())
        self.assertNotIn('openlitespeed', output.getvalue())

    def test_build_without_apply_only_plans(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            os_release = root / 'os-release'
            os_release.write_text('ID=ubuntu\nVERSION_ID="24.04"\n', encoding='utf-8')
            roles = root / 'roles.conf'
            roles.write_text('roles=web\n', encoding='utf-8')
            with mock.patch.object(PACKAGES, 'run_command') as run:
                output = io.StringIO()
                with redirect_stdout(output):
                    rc = CLI.main([
                        '--allow-non-root', '--config', str(root / 'missing.conf'),
                        '--os-release', str(os_release), '--roles-file', str(roles),
                        'build', 'web',
                    ])
                self.assertEqual(rc, 0)
                run.assert_not_called()
            self.assertIn('HostPanel build plan', output.getvalue())

    def test_update_panel_uses_signed_updater(self):
        completed = subprocess.CompletedProcess([], 10)
        with tempfile.TemporaryDirectory() as directory:
            updater = pathlib.Path(directory) / 'hostpanel-update'
            updater.write_text('#!/bin/sh\n', encoding='utf-8')
            updater.chmod(0o700)
            with mock.patch.object(CLI, 'run_command', return_value=completed) as run:
                rc = CLI.update_panel(False, updater)
            self.assertEqual(rc, 10)
            run.assert_called_once_with([str(updater), '--check'], check=False)

    def test_versions_json_has_stable_shape(self):
        options = dict(CONFIG.DEFAULT_OPTIONS)
        platform = CONFIG.Platform('debian', 'debian', '13')
        with tempfile.TemporaryDirectory() as directory:
            version = pathlib.Path(directory) / 'VERSION'
            version.write_text('3.4.1\n', encoding='utf-8')
            fake = [CONFIG.PackageState('nginx', 'nginx', '1.0', '1.1')]
            output = io.StringIO()
            with mock.patch.object(CLI, 'package_states', return_value=fake):
                with redirect_stdout(output):
                    CLI.print_versions(options, platform, version, True, {'web'})
            data = json.loads(output.getvalue())
            self.assertEqual(data['panel'], '3.4.1')
            self.assertEqual(data['packages'][0]['candidate'], '1.1')

    def test_openlitespeed_preflight_fails_before_mutation(self):
        options = dict(CONFIG.DEFAULT_OPTIONS)
        options['webserver'] = 'openlitespeed'
        platform = CONFIG.Platform('debian', 'ubuntu', '26.04')
        with mock.patch.object(OPERATIONS, 'candidate_version', return_value=None):
            with self.assertRaisesRegex(CONFIG.BuildError, 'no services were changed'):
                OPERATIONS.preflight_packages(
                    CONFIG.web_components(options), options, platform
                )

    def test_all_target_respects_roles_and_default_mode(self):
        options = dict(CONFIG.DEFAULT_OPTIONS)
        components = CONFIG.selected_components(options, {'web', 'dns'})
        self.assertEqual(components, ['nginx', 'apache', 'php', 'dns'])
        self.assertNotIn('openlitespeed', components)
        self.assertNotIn('mail', components)

    def test_runtime_patcher_is_structural_and_idempotent(self):
        webserver_fixture = r'''from pathlib import Path
import subprocess
MODES = ("nginx", "apache", "hybrid", "openlitespeed")
OLS_PROXY_VHOST = """server {{
}}
"""

def mode_of(domain):
    has_nginx = nginx_vhost.exists()
    has_apache = apache_vhost.exists()
    proxies = has_nginx and "@apache" in (NGINX_AVAIL / domain).read_text() \
        if has_nginx and (NGINX_AVAIL / domain).exists() else False

    if has_nginx and has_apache and proxies:
        return "hybrid"
    if has_apache and not has_nginx:
        return "apache"
    return "nginx"
def set_mode(mode):
        if mode == "apache":
            apply_apache(domain, docroot, socket)
            run([binary("sudo"), str(ROOT_HELPER), "nginx-site", "disable", domain])
            reload_service("nginx")
            remove_openlitespeed(domain)

NOTES = {
            "apache": "Apache serves everything, so .htaccess works. Uses more "
                      "memory per request than nginx.",
}
'''
        main_fixture = """def add_domain():
    reload_service("nginx")
    store.claim(user["user_id"], "domain", domain)
    return {"ok": True, "domain": domain, "docroot": str(docroot)}
"""
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            webserver = root / 'webserver.py'
            main = root / 'main.py'
            webserver.write_text(webserver_fixture, encoding='utf-8')
            main.write_text(main_fixture, encoding='utf-8')
            with mock.patch.object(PATCH, 'trusted_file', return_value=None):
                PATCH.patch_webserver(webserver)
                PATCH.patch_main(main)
                first_web = webserver.read_text()
                first_main = main.read_text()
                PATCH.patch_webserver(webserver)
                PATCH.patch_main(main)
            self.assertEqual(first_web, webserver.read_text())
            self.assertEqual(first_main, main.read_text())
            self.assertIn('DEFAULT_MODE_FILE', first_web)
            self.assertIn('APACHE_EDGE_VHOST', first_web)
            self.assertIn('APACHE_EDGE_TLS_VHOST', first_web)
            self.assertIn('apache_edge_vhost(', first_web)
            self.assertIn('# HostPanel Apache-only edge', first_web)
            self.assertIn('nginx rejected the Apache edge configuration', first_web)
            self.assertIn('target_mode = webserver.default_mode()', first_main)

    def test_service_reconciliation_disables_unused_backends(self):
        platform = CONFIG.Platform('debian', 'ubuntu', '24.04')
        log = pathlib.Path('/tmp/test-hostpanel-build.log')
        for mode, required, disabled in (
            ('nginx_apache', {'nginx.service', 'apache2.service'}, {'lsws.service'}),
            ('apache', {'nginx.service', 'apache2.service'}, {'lsws.service'}),
            ('nginx', {'nginx.service'}, {'apache2.service', 'lsws.service'}),
            ('openlitespeed', {'nginx.service', 'lsws.service'}, {'apache2.service'}),
        ):
            options = dict(CONFIG.DEFAULT_OPTIONS)
            options['webserver'] = mode
            calls: list[list[str]] = []
            with mock.patch.object(
                OPERATIONS, 'run_command',
                side_effect=lambda command, **kwargs: calls.append(command)
                or subprocess.CompletedProcess(command, 0, '', ''),
            ):
                OPERATIONS.reconcile_webserver_services(options, platform, log)
            joined = {' '.join(command) for command in calls}
            for service in required:
                self.assertTrue(
                    any('enable --now ' + service in line or 'is-active --quiet ' + service in line
                        for line in joined),
                    (mode, service, joined),
                )
            for service in disabled:
                self.assertTrue(
                    any('disable --now ' + service in line for line in joined),
                    (mode, service, joined),
                )

    def test_roles_file_is_strict(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / 'roles.conf'
            path.write_text('roles=web,dns\n', encoding='utf-8')
            self.assertEqual(CONFIG.read_roles(path), {'web', 'dns'})
            path.write_text('roles=web,root\n', encoding='utf-8')
            with self.assertRaises(CONFIG.BuildError):
                CONFIG.read_roles(path)

    def test_unsupported_component_fails_closed(self):
        with self.assertRaises(CONFIG.BuildError):
            CONFIG.expand_component('kernel', dict(CONFIG.DEFAULT_OPTIONS))


if __name__ == '__main__':
    unittest.main()
