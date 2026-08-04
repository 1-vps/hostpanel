from __future__ import annotations

import pathlib
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import hostpanel_build_entry as entry
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


class EntryCommandGuardTests(unittest.TestCase):
    @staticmethod
    def compatibility(options: dict[str, str]) -> None:
        if options.get('webserver') == 'nginx' and options.get('varnish') == 'on':
            raise BuildError('incompatible Varnish selection')

    def test_set_can_repair_an_existing_incompatible_configuration(self) -> None:
        parsed = SimpleNamespace(command='set', key='varnish', value='off')
        with mock.patch.object(
            entry.cli, 'validate_option_compatibility',
            side_effect=self.compatibility,
        ), mock.patch.object(
            entry.cli, 'validate_value', return_value='off',
        ):
            with entry._command_runtime_guards(parsed, pathlib.Path('/config')):
                entry.cli.validate_option_compatibility({
                    'webserver': 'nginx', 'varnish': 'on',
                })
                entry.cli.validate_option_compatibility({
                    'webserver': 'nginx', 'varnish': 'off',
                })

    def test_set_does_not_bypass_unrepaired_incompatibility(self) -> None:
        parsed = SimpleNamespace(command='set', key='mongodb', value='off')
        with mock.patch.object(
            entry.cli, 'validate_option_compatibility',
            side_effect=self.compatibility,
        ), mock.patch.object(
            entry.cli, 'validate_value', return_value='off',
        ):
            with entry._command_runtime_guards(parsed, pathlib.Path('/config')):
                with self.assertRaisesRegex(
                    BuildError, 'incompatible Varnish selection'
                ):
                    entry.cli.validate_option_compatibility({
                        'webserver': 'nginx',
                        'varnish': 'on',
                        'mongodb': '8.0',
                    })

    def test_validate_all_checks_disabled_mongodb_before_doctor(self) -> None:
        parsed = SimpleNamespace(
            command='validate', component='all', roles_file='/roles'
        )
        events: list[str] = []
        with mock.patch.object(
            entry, 'read_config', return_value={'mongodb': 'off'},
        ), mock.patch.object(
            entry, 'read_roles', return_value={'database'},
        ), mock.patch.object(
            entry.cli, 'validate_mongodb',
            side_effect=lambda _log: events.append('mongodb'),
        ), mock.patch.object(
            entry.cli, 'run_doctor',
            side_effect=lambda *_args: events.append('doctor'),
        ):
            with entry._command_runtime_guards(parsed, pathlib.Path('/config')):
                entry.cli.run_doctor(
                    pathlib.Path('/log'),
                    pathlib.Path('/python'),
                    pathlib.Path('/doctor'),
                )
        self.assertEqual(events, ['mongodb', 'doctor'])

    def test_validate_all_does_not_duplicate_enabled_mongodb_check(self) -> None:
        parsed = SimpleNamespace(
            command='validate', component='all', roles_file='/roles'
        )
        events: list[str] = []
        with mock.patch.object(
            entry, 'read_config', return_value={'mongodb': '8.0'},
        ), mock.patch.object(
            entry, 'read_roles', return_value={'database'},
        ), mock.patch.object(
            entry.cli, 'validate_mongodb',
            side_effect=lambda _log: events.append('mongodb'),
        ), mock.patch.object(
            entry.cli, 'run_doctor',
            side_effect=lambda *_args: events.append('doctor'),
        ):
            with entry._command_runtime_guards(parsed, pathlib.Path('/config')):
                entry.cli.run_doctor(
                    pathlib.Path('/log'),
                    pathlib.Path('/python'),
                    pathlib.Path('/doctor'),
                )
        self.assertEqual(events, ['doctor'])


if __name__ == '__main__':
    unittest.main()
