from __future__ import annotations

import importlib.util
import pathlib
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

_IMPL_PATH = pathlib.Path(__file__).with_name(
    '_custombuild_openlitespeed_tests_impl.py'
)
_SPEC = importlib.util.spec_from_file_location(
    '_custombuild_openlitespeed_tests_impl', _IMPL_PATH
)
if _SPEC is None or _SPEC.loader is None:
    raise ImportError(f'cannot load OpenLiteSpeed test implementation: {_IMPL_PATH}')
_IMPL = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _IMPL
_SPEC.loader.exec_module(_IMPL)


def _apache_mode_reads_runtime_implementation(self) -> None:
    options = dict(_IMPL.config.DEFAULT_OPTIONS)
    options['webserver'] = 'apache'
    self.assertEqual(
        _IMPL.config.web_components(options),
        ['nginx', 'apache', 'php'],
    )
    patcher = (
        _IMPL.TOOLS / 'patch_custombuild_runtime_impl.py'
    ).read_text(encoding='utf-8')
    self.assertIn('# HostPanel Apache-only edge', patcher)
    self.assertIn('proxy_pass http://127.0.0.1:{port}', patcher)
    self.assertIn('APACHE_EDGE_REDIRECT_VHOST', patcher)
    self.assertIn('nginx rejected the Apache edge configuration', patcher)


def _prepare_tree(directory: str, versions: tuple[str, ...]):
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
    for version in versions:
        binary = root / f'lsphp{version}/bin/lsphp'
        binary.parent.mkdir(parents=True)
        binary.write_text('#!/bin/sh\nexit 0\n')
        binary.chmod(0o755)
    return root, main, admin


def _prepare_openlitespeed_writes_private_global_configuration(self) -> None:
    with tempfile.TemporaryDirectory() as directory:
        root, main, admin = _prepare_tree(directory, ('85', '84'))
        options = dict(_IMPL.config.DEFAULT_OPTIONS)
        options['webserver'] = 'openlitespeed'
        options['php_versions'] = '8.5,8.4'
        commands: list[list[str]] = []
        state = pathlib.Path(directory) / 'lsphp-versions'
        with mock.patch.object(_IMPL.web.os, 'geteuid', return_value=0), \
             mock.patch.object(
                 _IMPL.web, 'trusted_root_file',
                 side_effect=lambda path: path.lstat(),
             ), mock.patch.object(_IMPL.web.os, 'fchown'), \
             mock.patch.object(_IMPL.web.os, 'chown'), \
             mock.patch.object(
                 _IMPL.web, 'group_gid',
                 side_effect=lambda name, required=False: 0,
             ), mock.patch.object(_IMPL.web, 'OLS_ROOT', root), \
             mock.patch.object(_IMPL.web, 'OLS_MAIN', main), \
             mock.patch.object(_IMPL.web, 'OLS_ADMIN', admin), \
             mock.patch.object(
                 _IMPL.web, 'OLS_HOSTPANEL', root / 'conf/hostpanel'
             ), mock.patch.object(
                 _IMPL.web, 'OLS_VHOSTS', root / 'conf/vhosts'
             ), mock.patch.object(
                 _IMPL.web, 'OLS_REGISTRY',
                 root / 'conf/hostpanel/hostpanel.conf',
             ), mock.patch.object(
                 _IMPL.web, 'OLS_STATE_ROOT', pathlib.Path(directory) / 'state'
             ), mock.patch.object(
                 _IMPL.web, 'OLS_MARKERS',
                 pathlib.Path(directory) / 'state/domains',
             ), mock.patch.object(
                 _IMPL.web, 'OLS_LOGS', pathlib.Path(directory) / 'logs'
             ), mock.patch.object(_IMPL.web, 'LSPHP_STATE', state), \
             mock.patch.object(
                 _IMPL.web, 'run',
                 side_effect=lambda command, check=True: commands.append(command)
                 or subprocess.CompletedProcess(command, 0, '', ''),
             ):
            _IMPL.web.prepare_openlitespeed(options)
        self.assertIn(_IMPL.web.OLS_INCLUDE, main.read_text())
        self.assertIn('127.0.0.1:7080', admin.read_text())
        self.assertIn(
            '127.0.0.1:8088',
            (root / 'conf/hostpanel/hostpanel.conf').read_text(),
        )
        self.assertEqual(
            (root / 'fcgi-bin/lsphp').resolve(),
            root / 'lsphp85/bin/lsphp',
        )
        self.assertEqual(commands, [[str(root / 'bin/openlitespeed'), '-t']])
        self.assertFalse(
            any('enable' in command or 'unmask' in command for command in commands)
        )


def _prepare_openlitespeed_restores_configs_on_validation_failure(self) -> None:
    with tempfile.TemporaryDirectory() as directory:
        root, main, admin = _prepare_tree(directory, ('85',))
        options = dict(_IMPL.config.DEFAULT_OPTIONS)
        options['webserver'] = 'openlitespeed'
        options['php_versions'] = '8.5'
        state_root = pathlib.Path(directory) / 'state'
        with mock.patch.object(_IMPL.web.os, 'geteuid', return_value=0), \
             mock.patch.object(
                 _IMPL.web, 'trusted_root_file',
                 side_effect=lambda path: path.lstat(),
             ), mock.patch.object(_IMPL.web.os, 'fchown'), \
             mock.patch.object(_IMPL.web.os, 'chown'), \
             mock.patch.object(
                 _IMPL.web, 'group_gid',
                 side_effect=lambda name, required=False: 0,
             ), mock.patch.object(_IMPL.web, 'OLS_ROOT', root), \
             mock.patch.object(_IMPL.web, 'OLS_MAIN', main), \
             mock.patch.object(_IMPL.web, 'OLS_ADMIN', admin), \
             mock.patch.object(
                 _IMPL.web, 'OLS_HOSTPANEL', root / 'conf/hostpanel'
             ), mock.patch.object(
                 _IMPL.web, 'OLS_VHOSTS', root / 'conf/vhosts'
             ), mock.patch.object(
                 _IMPL.web, 'OLS_REGISTRY',
                 root / 'conf/hostpanel/hostpanel.conf',
             ), mock.patch.object(_IMPL.web, 'OLS_STATE_ROOT', state_root), \
             mock.patch.object(
                 _IMPL.web, 'OLS_MARKERS', state_root / 'domains'
             ), mock.patch.object(
                 _IMPL.web, 'OLS_LOGS', pathlib.Path(directory) / 'logs'
             ), mock.patch.object(
                 _IMPL.web, 'LSPHP_STATE',
                 pathlib.Path(directory) / 'lsphp-versions',
             ), mock.patch.object(
                 _IMPL.web, 'run',
                 side_effect=_IMPL.config.BuildError('test failed'),
             ):
            with self.assertRaisesRegex(_IMPL.config.BuildError, 'test failed'):
                _IMPL.web.prepare_openlitespeed(options)
        self.assertEqual(main.read_text(), 'address *:8088\n')
        self.assertEqual(admin.read_text(), 'address *:7080\n')
        self.assertFalse((root / 'conf/hostpanel/hostpanel.conf').exists())


def _activation_happens_after_domain_reconciliation(self) -> None:
    source = (_IMPL.TOOLS / 'hostpanel_build_web.py').read_text(
        encoding='utf-8'
    )
    main_body = source.split('def main(', 1)[1]
    switch = main_body.index('webserver.set_mode(domain, target, admin)')
    validate = main_body.index("if mismatches:", switch)
    activate = main_body.index('activate_openlitespeed()', validate)
    self.assertLess(switch, validate)
    self.assertLess(validate, activate)
    prepare_body = source.split('def prepare_openlitespeed', 1)[1].split(
        'def _command_text', 1
    )[0]
    self.assertNotIn("'enable', '--now'", prepare_body)
    self.assertNotIn("'unmask', '--runtime'", prepare_body)


def _activation_rolls_back_service_state_on_failure(self) -> None:
    calls: list[list[str]] = []

    def run(command, check=True):
        calls.append(command)
        joined = ' '.join(command)
        if 'unmask --runtime' in joined:
            return subprocess.CompletedProcess(command, 0, '', '')
        if 'is-active' in joined:
            return subprocess.CompletedProcess(command, 3, 'inactive\n', '')
        if 'is-enabled' in joined:
            return subprocess.CompletedProcess(command, 1, 'disabled\n', '')
        if 'enable --now' in joined:
            raise _IMPL.config.BuildError('injected activation failure')
        return subprocess.CompletedProcess(command, 0, '', '')

    with mock.patch.object(_IMPL.web, 'run', side_effect=run):
        with self.assertRaisesRegex(
            _IMPL.config.BuildError, 'injected activation failure'
        ):
            _IMPL.web.activate_openlitespeed()
    self.assertEqual(
        calls[-2:],
        [
            ['systemctl', 'disable', 'lsws.service'],
            ['systemctl', 'stop', 'lsws.service'],
        ],
    )


_IMPL.CustomBuildOpenLiteSpeedTests.test_apache_mode_keeps_the_public_nginx_edge = (
    _apache_mode_reads_runtime_implementation
)
_IMPL.CustomBuildOpenLiteSpeedTests.test_prepare_openlitespeed_writes_private_global_configuration = (
    _prepare_openlitespeed_writes_private_global_configuration
)
_IMPL.CustomBuildOpenLiteSpeedTests.test_prepare_openlitespeed_restores_configs_on_validation_failure = (
    _prepare_openlitespeed_restores_configs_on_validation_failure
)
_IMPL.CustomBuildOpenLiteSpeedTests.test_activation_happens_after_domain_reconciliation = (
    _activation_happens_after_domain_reconciliation
)
_IMPL.CustomBuildOpenLiteSpeedTests.test_activation_rolls_back_service_state_on_failure = (
    _activation_rolls_back_service_state_on_failure
)

for _name in dir(_IMPL):
    _value = getattr(_IMPL, _name)
    if (
        isinstance(_value, type)
        and issubclass(_value, unittest.TestCase)
        and _value is not unittest.TestCase
    ):
        globals()[_name] = _value


if __name__ == '__main__':
    unittest.main()
