from __future__ import annotations

import importlib.util
import io
import json
import os
import pathlib
import stat
import subprocess
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'tools' / 'hostpanel-build.py'
INSTALLER = ROOT / 'tools' / 'install-hostpanel-build.sh'
DOC = ROOT / 'CUSTOMBUILD.md'
SPEC = importlib.util.spec_from_file_location('hostpanel_build', SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
import sys
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class HostPanelBuildTests(unittest.TestCase):
    def test_syntax_and_help(self):
        subprocess.run(['python3', '-m', 'py_compile', str(SCRIPT)], check=True)
        result = subprocess.run(['python3', str(SCRIPT), '--help'], check=True, capture_output=True, text=True)
        self.assertIn('CustomBuild-style', result.stdout)
        self.assertIn('update_versions', result.stdout)
        self.assertIn('update_panel', result.stdout)

    def test_installer_and_documentation_contract(self):
        installer = INSTALLER.read_text(encoding='utf-8')
        document = DOC.read_text(encoding='utf-8')
        self.assertIn('/usr/local/sbin/hostpanel-build', installer)
        self.assertIn('/opt/hostpanel/tools/hostpanel-build', installer)
        self.assertIn('chmod 0600 "$CONFIG"', installer)
        self.assertIn('python3 -m py_compile "$SOURCE"', installer)
        self.assertIn('hostpanel-build versions', document)
        self.assertIn('build all --apply', document)
        self.assertIn('update_panel --apply', document)
        self.assertIn('do not change packages or services', document)

    def test_config_parsing_is_strict(self):
        values = MODULE.parse_config_text('webservers=openlitespeed\nphp_versions=8.5,8.4\n')
        self.assertEqual(values['webservers'], 'openlitespeed')
        self.assertEqual(values['php_versions'], '8.5,8.4')
        with self.assertRaises(MODULE.BuildError):
            MODULE.parse_config_text('webservers=nginx\nwebservers=apache\n')
        with self.assertRaises(MODULE.BuildError):
            MODULE.parse_config_text('unknown=yes\n')
        with self.assertRaises(MODULE.BuildError):
            MODULE.parse_config_text('php_versions=8.5,8.5\n')

    def test_config_write_is_atomic_and_private(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / 'build.conf'
            options = dict(MODULE.DEFAULT_OPTIONS)
            options['mta'] = 'exim'
            MODULE.write_config(path, options)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(MODULE.read_config(path)['mta'], 'exim')
            leftovers = list(path.parent.glob('.build.conf.*'))
            self.assertEqual(leftovers, [])

    def test_component_mapping_debian_and_rhel(self):
        options = dict(MODULE.DEFAULT_OPTIONS)
        debian = MODULE.Platform('debian', 'ubuntu', '24.04')
        rhel = MODULE.Platform('rhel', 'rocky', '9')
        self.assertEqual(MODULE.component_packages('apache', options, debian), ['apache2'])
        self.assertEqual(MODULE.component_packages('apache', options, rhel), ['httpd'])
        self.assertIn('php8.5-fpm', MODULE.component_packages('php', options, debian))
        self.assertIn('php85-php-fpm', MODULE.component_packages('php', options, rhel))
        self.assertIn('bind9', MODULE.component_packages('dns', options, debian))
        self.assertIn('bind', MODULE.component_packages('dns', options, rhel))

    def test_plan_is_non_mutating(self):
        options = dict(MODULE.DEFAULT_OPTIONS)
        platform = MODULE.Platform('debian', 'ubuntu', '24.04')
        with mock.patch.object(MODULE, 'run_command') as run:
            output = io.StringIO()
            with redirect_stdout(output):
                MODULE.print_plan('web', options, platform)
            run.assert_not_called()
        self.assertIn('No changes are made without --apply.', output.getvalue())
        self.assertIn('nginx', output.getvalue())
        self.assertIn('php8.5-fpm', output.getvalue())

    def test_build_without_apply_only_plans(self):
        with tempfile.TemporaryDirectory() as directory:
            os_release = pathlib.Path(directory) / 'os-release'
            os_release.write_text('ID=ubuntu\nVERSION_ID="24.04"\n', encoding='utf-8')
            config = pathlib.Path(directory) / 'missing.conf'
            roles = pathlib.Path(directory) / 'roles.conf'
            roles.write_text('roles=web\n', encoding='utf-8')
            with mock.patch.object(MODULE, 'run_command') as run:
                output = io.StringIO()
                with redirect_stdout(output):
                    rc = MODULE.main(['--allow-non-root', '--config', str(config), '--os-release', str(os_release), '--roles-file', str(roles), 'build', 'nginx'])
                self.assertEqual(rc, 0)
                run.assert_not_called()
            self.assertIn('HostPanel build plan', output.getvalue())

    def test_update_panel_uses_signed_updater(self):
        completed = subprocess.CompletedProcess([], 10)
        with tempfile.TemporaryDirectory() as directory:
            updater = pathlib.Path(directory) / 'hostpanel-update'
            updater.write_text('#!/bin/sh\n', encoding='utf-8')
            updater.chmod(0o700)
            with mock.patch.object(MODULE, 'run_command', return_value=completed) as run:
                rc = MODULE.update_panel(False, updater)
            self.assertEqual(rc, 10)
            run.assert_called_once_with([str(updater), '--check'], check=False)

    def test_versions_json_has_stable_shape(self):
        options = dict(MODULE.DEFAULT_OPTIONS)
        platform = MODULE.Platform('debian', 'debian', '13')
        with tempfile.TemporaryDirectory() as directory:
            version = pathlib.Path(directory) / 'VERSION'
            version.write_text('3.4.1\n', encoding='utf-8')
            fake = [MODULE.PackageState('nginx', 'nginx', '1.0', '1.1')]
            output = io.StringIO()
            with mock.patch.object(MODULE, 'package_states', return_value=fake):
                with redirect_stdout(output):
                    MODULE.print_versions(options, platform, version, True, {'web'})
            data = json.loads(output.getvalue())
            self.assertEqual(data['panel'], '3.4.1')
            self.assertEqual(data['packages'][0]['candidate'], '1.1')

    def test_all_target_respects_installed_roles(self):
        options = dict(MODULE.DEFAULT_OPTIONS)
        components = MODULE.selected_components(options, {'web', 'dns'})
        self.assertEqual(components, ['nginx', 'apache', 'openlitespeed', 'php', 'dns'])
        self.assertNotIn('mail', components)
        self.assertNotIn('database', components)

    def test_roles_file_is_strict(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / 'roles.conf'
            path.write_text('roles=web,dns\n', encoding='utf-8')
            self.assertEqual(MODULE.read_roles(path), {'web', 'dns'})
            path.write_text('roles=web,root\n', encoding='utf-8')
            with self.assertRaises(MODULE.BuildError):
                MODULE.read_roles(path)

    def test_unsupported_component_fails_closed(self):
        with self.assertRaises(MODULE.BuildError):
            MODULE.expand_component('kernel', dict(MODULE.DEFAULT_OPTIONS))


if __name__ == '__main__':
    unittest.main()
