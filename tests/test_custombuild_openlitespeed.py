from __future__ import annotations

import importlib
import pathlib
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
sys.path.insert(0, str(TOOLS))

import hostpanel_build_config as config
import hostpanel_build_operations as operations

store = types.ModuleType('store')
store.connect = lambda: None
modules = types.ModuleType('modules')
webserver_stub = types.SimpleNamespace()
modules.webserver = webserver_stub
sys.modules['store'] = store
sys.modules['modules'] = modules
web = importlib.import_module('hostpanel_build_web')


class CustomBuildOpenLiteSpeedTests(unittest.TestCase):
    def test_base_mode_is_nginx_apache(self):
        self.assertEqual(config.DEFAULT_OPTIONS['webserver'], 'nginx_apache')
        self.assertEqual(
            config.web_components(config.DEFAULT_OPTIONS),
            ['nginx', 'apache', 'php'],
        )

    def test_apache_mode_keeps_the_public_nginx_edge(self):
        options = dict(config.DEFAULT_OPTIONS)
        options['webserver'] = 'apache'
        self.assertEqual(
            config.web_components(options),
            ['nginx', 'apache', 'php'],
        )
        patcher = (TOOLS / 'patch_custombuild_runtime.py').read_text(encoding='utf-8')
        self.assertIn('# HostPanel Apache-only edge', patcher)
        self.assertIn('proxy_pass http://127.0.0.1:{port}', patcher)
        self.assertIn('APACHE_EDGE_REDIRECT_VHOST', patcher)
        self.assertIn('nginx rejected the Apache edge configuration', patcher)

    def test_openlitespeed_includes_versioned_lsphp_runtime_packages(self):
        options = dict(config.DEFAULT_OPTIONS)
        options['webserver'] = 'openlitespeed'
        options['php_versions'] = '8.5,8.4'
        packages = config.component_packages(
            'openlitespeed', options, config.Platform('debian', 'ubuntu', '24.04')
        )
        for package in (
            'openlitespeed', 'lsphp85', 'lsphp85-common', 'lsphp85-mysql',
            'lsphp85-opcache', 'lsphp84', 'lsphp84-common', 'lsphp84-mysql',
        ):
            self.assertIn(package, packages)
        rhel_packages = config.component_packages(
            'openlitespeed', options, config.Platform('rhel', 'rocky', '9')
        )
        self.assertIn('lsphp85-mysqlnd', rhel_packages)
        self.assertIn('lsphp85-pdo', rhel_packages)
        self.assertNotIn('lsphp85-mysql', rhel_packages)

    def test_missing_lsphp_fails_before_service_mutation(self):
        options = dict(config.DEFAULT_OPTIONS)
        options['webserver'] = 'openlitespeed'
        platform = config.Platform('debian', 'ubuntu', '26.04')
        with mock.patch.object(operations, 'candidate_version', return_value=None):
            with self.assertRaisesRegex(config.BuildError, 'no services were changed'):
                operations.preflight_packages(
                    config.web_components(options), options, platform
                )

    def test_main_config_moves_example_listener_and_adds_private_include(self):
        original = '''listener Default {\n  address *:8088\n}\n'''
        updated = web.rewrite_main_config(original)
        self.assertIn('address 127.0.0.1:8099', updated)
        self.assertIn('useIpInProxyHeader 1', updated)
        self.assertIn(web.OLS_INCLUDE, updated)
        self.assertEqual(web.rewrite_main_config(updated), updated)

    def test_main_config_rejects_custom_8088_collision(self):
        with self.assertRaisesRegex(config.BuildError, 'custom listener'):
            web.rewrite_main_config('address 192.0.2.9:8088\n')

    def test_webadmin_is_forced_to_loopback(self):
        updated = web.rewrite_admin_config('address *:7080\n')
        self.assertEqual(updated, 'address 127.0.0.1:7080\n')
        self.assertEqual(web.rewrite_admin_config(updated), updated)

    def test_openlitespeed_is_runtime_masked_until_reconciliation(self):
        source = (TOOLS / 'hostpanel_build_operations.py').read_text(encoding='utf-8')
        mask = source.index("mask_openlitespeed(log_path)")
        reinstall = source.index('reinstall_packages(', mask)
        reconcile = source.index('reconcile_webserver(', reinstall)
        services = source.index('reconcile_webserver_services(', reconcile)
        unmask = source.index('unmask_openlitespeed(log_path)', services)
        self.assertLess(mask, reinstall)
        self.assertLess(reinstall, reconcile)
        self.assertLess(reconcile, services)
        self.assertLess(services, unmask)
        self.assertIn("if item != 'openlitespeed':", source)

    def test_unused_backends_are_disabled_after_success(self):
        options = dict(config.DEFAULT_OPTIONS)
        platform = config.Platform('debian', 'ubuntu', '24.04')
        log = pathlib.Path('/tmp/hostpanel-build-test.log')
        cases = {
            'nginx_apache': {'disable': {'lsws.service'}},
            'apache': {'disable': {'lsws.service'}},
            'nginx': {'disable': {'apache2.service', 'lsws.service'}},
            'openlitespeed': {'disable': {'apache2.service'}},
        }
        for mode, expected in cases.items():
            options['webserver'] = mode
            commands: list[list[str]] = []
            with mock.patch.object(
                operations, 'run_command',
                side_effect=lambda command, **kwargs: commands.append(command)
                or subprocess.CompletedProcess(command, 0, '', ''),
            ):
                operations.reconcile_webserver_services(options, platform, log)
            flattened = [' '.join(command) for command in commands]
            for service in expected['disable']:
                self.assertTrue(
                    any(f'disable --now {service}' in line for line in flattened),
                    (mode, service, flattened),
                )

    def test_domain_switch_has_reverse_rollback(self):
        source = (TOOLS / 'hostpanel_build_web.py').read_text(encoding='utf-8')
        self.assertIn('for domain in reversed(changed):', source)
        self.assertIn("webserver.set_mode(domain, previous[domain], admin)", source)
        self.assertIn('webserver switch failed and was rolled back', source)

    def test_prepare_openlitespeed_writes_private_global_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory) / 'lsws'
            (root / 'bin').mkdir(parents=True)
            (root / 'conf').mkdir(parents=True)
            (root / 'admin/conf').mkdir(parents=True)
            (root / 'bin/openlitespeed').write_text('#!/bin/sh\nexit 0\n')
            (root / 'bin/openlitespeed').chmod(0o755)
            (root / 'conf/httpd_config.conf').write_text('address *:8088\n')
            (root / 'admin/conf/admin_config.conf').write_text('address *:7080\n')
            for version in ('85', '84'):
                binary = root / f'lsphp{version}/bin/lsphp'
                binary.parent.mkdir(parents=True)
                binary.write_text('#!/bin/sh\nexit 0\n')
                binary.chmod(0o755)
            options = dict(config.DEFAULT_OPTIONS)
            options['webserver'] = 'openlitespeed'
            options['php_versions'] = '8.5,8.4'
            commands: list[list[str]] = []
            state = pathlib.Path(directory) / 'lsphp-versions'
            with mock.patch.object(web, 'group_gid', side_effect=lambda name, required=False: 0), \
                 mock.patch.object(web, 'OLS_ROOT', root), \
                 mock.patch.object(web, 'OLS_MAIN', root / 'conf/httpd_config.conf'), \
                 mock.patch.object(web, 'OLS_ADMIN', root / 'admin/conf/admin_config.conf'), \
                 mock.patch.object(web, 'OLS_HOSTPANEL', root / 'conf/hostpanel'), \
                 mock.patch.object(web, 'OLS_VHOSTS', root / 'conf/vhosts'), \
                 mock.patch.object(web, 'OLS_REGISTRY', root / 'conf/hostpanel/hostpanel.conf'), \
                 mock.patch.object(web, 'OLS_STATE_ROOT', pathlib.Path(directory) / 'state'), \
                 mock.patch.object(web, 'OLS_MARKERS', pathlib.Path(directory) / 'state/domains'), \
                 mock.patch.object(web, 'OLS_LOGS', pathlib.Path(directory) / 'logs'), \
                 mock.patch.object(web, 'LSPHP_STATE', state), \
                 mock.patch.object(web.os, 'geteuid', return_value=0), \
                 mock.patch.object(web.os, 'chown'), \
                 mock.patch.object(web.os, 'fchown'), \
                 mock.patch.object(web, 'trusted_root_file', side_effect=lambda path: path.lstat()), \
                 mock.patch.object(web, 'run', side_effect=lambda command, check=True: commands.append(command) or types.SimpleNamespace(returncode=0, stdout='', stderr='')):
                web.prepare_openlitespeed(options)
            self.assertIn(web.OLS_INCLUDE, (root / 'conf/httpd_config.conf').read_text())
            self.assertIn('127.0.0.1:7080', (root / 'admin/conf/admin_config.conf').read_text())
            self.assertIn('127.0.0.1:8088', (root / 'conf/hostpanel/hostpanel.conf').read_text())
            self.assertEqual((root / 'fcgi-bin/lsphp').resolve(), root / 'lsphp85/bin/lsphp')
            self.assertIn(['systemctl', 'enable', '--now', 'lsws.service'], commands)

    def test_prepare_openlitespeed_restores_configs_on_validation_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory) / 'lsws'
            (root / 'bin').mkdir(parents=True)
            (root / 'conf').mkdir(parents=True)
            (root / 'admin/conf').mkdir(parents=True)
            (root / 'bin/openlitespeed').write_text('#!/bin/sh\nexit 0\n')
            (root / 'bin/openlitespeed').chmod(0o755)
            main = root / 'conf/httpd_config.conf'
            admin = root / 'admin/conf/admin_config.conf'
            main.write_text('address *:8088\n')
            admin.write_text('address *:7080\n')
            binary = root / 'lsphp85/bin/lsphp'
            binary.parent.mkdir(parents=True)
            binary.write_text('#!/bin/sh\nexit 0\n')
            binary.chmod(0o755)
            options = dict(config.DEFAULT_OPTIONS)
            options['webserver'] = 'openlitespeed'
            options['php_versions'] = '8.5'
            state_root = pathlib.Path(directory) / 'state'
            with mock.patch.object(web, 'group_gid', side_effect=lambda name, required=False: 0), \
                 mock.patch.object(web, 'OLS_ROOT', root), \
                 mock.patch.object(web, 'OLS_MAIN', main), \
                 mock.patch.object(web, 'OLS_ADMIN', admin), \
                 mock.patch.object(web, 'OLS_HOSTPANEL', root / 'conf/hostpanel'), \
                 mock.patch.object(web, 'OLS_VHOSTS', root / 'conf/vhosts'), \
                 mock.patch.object(web, 'OLS_REGISTRY', root / 'conf/hostpanel/hostpanel.conf'), \
                 mock.patch.object(web, 'OLS_STATE_ROOT', state_root), \
                 mock.patch.object(web, 'OLS_MARKERS', state_root / 'domains'), \
                 mock.patch.object(web, 'OLS_LOGS', pathlib.Path(directory) / 'logs'), \
                 mock.patch.object(web, 'LSPHP_STATE', pathlib.Path(directory) / 'lsphp-versions'), \
                 mock.patch.object(web.os, 'geteuid', return_value=0), \
                 mock.patch.object(web.os, 'chown'), \
                 mock.patch.object(web.os, 'fchown'), \
                 mock.patch.object(web, 'trusted_root_file', side_effect=lambda path: path.lstat()), \
                 mock.patch.object(web, 'run', side_effect=config.BuildError('test failed')):
                with self.assertRaisesRegex(config.BuildError, 'test failed'):
                    web.prepare_openlitespeed(options)
            self.assertEqual(main.read_text(), 'address *:8088\n')
            self.assertEqual(admin.read_text(), 'address *:7080\n')
            self.assertFalse((root / 'conf/hostpanel/hostpanel.conf').exists())

    def test_check_path_does_not_write_lsphp_state(self):
        source = (TOOLS / 'hostpanel_build_web.py').read_text(encoding='utf-8')
        check_body = source.split('def check_openlitespeed', 1)[1].split('def rollback_domains', 1)[0]
        self.assertIn('validate_lsphp_runtimes(options)', check_body)
        self.assertNotIn('ensure_lsphp_runtimes(options)', check_body)
        self.assertNotIn('write_new_root_file', check_body)


if __name__ == '__main__':
    unittest.main()
