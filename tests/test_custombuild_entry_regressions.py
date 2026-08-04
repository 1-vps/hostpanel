from __future__ import annotations

import contextlib
import pathlib
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import hostpanel_build_cli as cli
import hostpanel_build_extras_state as state
from hostpanel_build_config import BuildError


class MongoDbInstalledHardenerTests(unittest.TestCase):
    def test_state_install_keeps_quoted_yaml_sections_fail_closed(self) -> None:
        original_impl = state._IMPL.harden_mongod_config
        original_base = state._IMPL.base.harden_mongod_config
        text = (
            '"net":\n'
            '  bindIp: 0.0.0.0\n'
            "'security':\n"
            '  authorization: disabled\n'
        )
        try:
            with mock.patch.object(state, '_ORIGINAL_INSTALL') as installer:
                state.install()
            installer.assert_called_once_with()
            self.assertIs(
                state._IMPL.harden_mongod_config,
                state._SAFE_HARDEN_MONGOD_CONFIG,
            )
            self.assertIs(
                state._IMPL.base.harden_mongod_config,
                state._SAFE_HARDEN_MONGOD_CONFIG,
            )
            with self.assertRaisesRegex(BuildError, 'non-loopback bindIp'):
                state._IMPL.harden_mongod_config(text)
        finally:
            state._IMPL.harden_mongod_config = original_impl
            state._IMPL.base.harden_mongod_config = original_base


class CliRegressionTests(unittest.TestCase):
    def test_set_can_repair_existing_incompatible_configuration(self) -> None:
        current = {'webserver': 'nginx', 'varnish': 'on'}
        with mock.patch.object(cli.os, 'geteuid', return_value=0), \
             mock.patch.object(cli, 'read_config', return_value=current), \
             mock.patch.object(cli, 'require_root'), \
             mock.patch.object(cli, 'validate_value', return_value='off'), \
             mock.patch.object(cli, 'write_config') as write_config:
            result = cli.main([
                '--config', '/config', 'set', 'varnish', 'off',
            ])

        self.assertEqual(result, 0)
        write_config.assert_called_once_with(
            pathlib.Path('/config'),
            {'webserver': 'nginx', 'varnish': 'off'},
        )

    def test_set_still_rejects_new_incompatible_configuration(self) -> None:
        current = {'webserver': 'apache', 'varnish': 'on'}
        with mock.patch.object(cli.os, 'geteuid', return_value=0), \
             mock.patch.object(cli, 'read_config', return_value=current), \
             mock.patch.object(cli, 'require_root'), \
             mock.patch.object(cli, 'validate_value', return_value='nginx'), \
             mock.patch.object(cli, 'write_config') as write_config:
            result = cli.main([
                '--config', '/config', 'set', 'webserver', 'nginx',
            ])

        self.assertEqual(result, 1)
        write_config.assert_not_called()

    def test_validate_all_checks_mongodb_when_configured_off(self) -> None:
        options = {
            'webserver': 'nginx_apache',
            'varnish': 'off',
            'mongodb': 'off',
        }
        platform = SimpleNamespace(family='debian')
        with mock.patch.object(cli.os, 'geteuid', return_value=0), \
             mock.patch.object(cli, 'read_config', return_value=options), \
             mock.patch.object(cli, 'detect_platform', return_value=platform), \
             mock.patch.object(cli, 'read_roles', return_value={'database'}), \
             mock.patch.object(cli, 'expand_component', return_value=[]), \
             mock.patch.object(
                 cli, 'acquire_lock', return_value=contextlib.nullcontext()
             ), mock.patch.object(cli, 'validate_mongodb') as validate_mongodb, \
             mock.patch.object(cli, 'run_doctor') as run_doctor:
            result = cli.main(['validate', 'all'])

        self.assertEqual(result, 0)
        validate_mongodb.assert_called_once_with(
            pathlib.Path('/var/log/hostpanel-build.log')
        )
        run_doctor.assert_called_once()


if __name__ == '__main__':
    unittest.main()
