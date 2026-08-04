from __future__ import annotations

import importlib.util
import pathlib
import subprocess
import sys
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
WRAPPER = TOOLS / 'hostpanel_build_web.py'
INSTALLER = TOOLS / 'install-hostpanel-build.sh'


def load_wrapper(name: str):
    store = types.ModuleType('store')
    store.connect = lambda: None
    modules = types.ModuleType('modules')
    modules.webserver = types.SimpleNamespace()
    previous_store = sys.modules.get('store')
    previous_modules = sys.modules.get('modules')
    previous_impl = sys.modules.get('_hostpanel_build_web_impl')
    sys.modules['store'] = store
    sys.modules['modules'] = modules
    try:
        spec = importlib.util.spec_from_file_location(name, WRAPPER)
        if spec is None or spec.loader is None:
            raise RuntimeError('could not load web transaction wrapper')
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        if previous_store is None:
            sys.modules.pop('store', None)
        else:
            sys.modules['store'] = previous_store
        if previous_modules is None:
            sys.modules.pop('modules', None)
        else:
            sys.modules['modules'] = previous_modules
        if previous_impl is None:
            sys.modules.pop('_hostpanel_build_web_impl', None)
        else:
            sys.modules['_hostpanel_build_web_impl'] = previous_impl


class OpenLiteSpeedTransactionWrapperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.web = load_wrapper('_hostpanel_web_transaction_test')

    @classmethod
    def tearDownClass(cls) -> None:
        sys.modules.pop('_hostpanel_web_transaction_test', None)

    def test_service_rollback_continues_after_operating_system_error(self) -> None:
        calls: list[list[str]] = []

        def run(command, **_kwargs):
            calls.append(command)
            if command[1] == 'enable':
                raise OSError('systemctl unavailable')
            return subprocess.CompletedProcess(command, 0, '', '')

        with mock.patch.object(self.web, 'run', side_effect=run):
            errors = self.web._restore_service_state(
                'lsws.service', was_active=True, was_enabled=True
            )

        self.assertEqual(
            calls,
            [
                ['systemctl', 'enable', 'lsws.service'],
                ['systemctl', 'start', 'lsws.service'],
            ],
        )
        self.assertEqual(len(errors), 1)
        self.assertIn('systemctl unavailable', errors[0])

    def test_late_domain_failure_restores_preparation_snapshot(self) -> None:
        snapshots = [(pathlib.Path('/main'), ('file', 'old'))]
        server = types.SimpleNamespace(
            mode_of=mock.Mock(return_value='hybrid'),
            set_mode=mock.Mock(side_effect=OSError('domain switch failed')),
        )
        with mock.patch.object(
            self.web, 'read_config', return_value={'webserver': 'openlitespeed'}
        ), mock.patch.object(
            self.web, 'managed_domains', return_value=['example.test']
        ), mock.patch.object(
            self.web, 'webserver', server
        ), mock.patch.object(
            self.web, '_preparation_snapshots', return_value=snapshots
        ), mock.patch.object(
            self.web, 'prepare_openlitespeed'
        ) as prepare, mock.patch.object(
            self.web, 'rollback_domains', return_value=[]
        ) as domains, mock.patch.object(
            self.web, '_restore_preparation', return_value=[]
        ) as restore:
            with self.assertRaisesRegex(
                self.web.BuildError,
                'webserver switch failed and was rolled back: domain switch failed',
            ):
                self.web.main(['openlitespeed'])

        prepare.assert_called_once()
        domains.assert_called_once()
        restore.assert_called_once_with(snapshots)

    def test_domain_and_preparation_rollback_errors_are_aggregated(self) -> None:
        snapshots = [(pathlib.Path('/main'), ('file', 'old'))]
        server = types.SimpleNamespace(
            mode_of=mock.Mock(return_value='hybrid'),
            set_mode=mock.Mock(side_effect=RuntimeError('switch failed')),
        )
        with mock.patch.object(
            self.web, 'read_config', return_value={'webserver': 'openlitespeed'}
        ), mock.patch.object(
            self.web, 'managed_domains', return_value=['example.test']
        ), mock.patch.object(
            self.web, 'webserver', server
        ), mock.patch.object(
            self.web, '_preparation_snapshots', return_value=snapshots
        ), mock.patch.object(
            self.web, 'prepare_openlitespeed'
        ), mock.patch.object(
            self.web, 'rollback_domains', return_value=['example.test: domain rollback']
        ), mock.patch.object(
            self.web, '_restore_preparation', return_value=['/main: preparation rollback']
        ):
            with self.assertRaises(self.web.BuildError) as raised:
                self.web.main(['openlitespeed'])

        message = str(raised.exception)
        self.assertIn('example.test: domain rollback', message)
        self.assertIn('/main: preparation rollback', message)

    def test_snapshot_covers_every_preparation_mutation(self) -> None:
        paths: list[tuple[pathlib.Path, bool]] = []

        def capture(path, *, allow_symlink=False):
            paths.append((path, allow_symlink))
            return ('missing',)

        with mock.patch.object(self.web, '_snapshot_path', side_effect=capture):
            snapshots = self.web._preparation_snapshots()

        self.assertEqual(len(snapshots), 5)
        self.assertEqual(
            paths,
            [
                (self.web.OLS_MAIN, False),
                (self.web.OLS_ADMIN, False),
                (self.web.OLS_REGISTRY, False),
                (self.web.OLS_ROOT / 'fcgi-bin/lsphp', True),
                (self.web.LSPHP_STATE, False),
            ],
        )

    def test_installed_layout_contains_preserved_implementation(self) -> None:
        source = INSTALLER.read_text(encoding='utf-8')
        self.assertIn('hostpanel_build_web_impl.py', source)
        self.assertIn("'_hostpanel_build_web_impl'", WRAPPER.read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
