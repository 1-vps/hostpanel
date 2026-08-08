from __future__ import annotations

import importlib.util
import os
import pathlib
import subprocess
import sys
import tempfile
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

        with mock.patch.object(
            self.web, 'run', side_effect=run
        ), mock.patch.object(
            self.web, '_service_enablement_state', return_value='enabled'
        ), mock.patch.object(
            self.web, '_service_active', return_value=True
        ):
            errors = self.web._restore_service_state(
                'lsws.service', was_active=True,
                enablement_state='enabled',
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

    def test_enabled_runtime_is_restored_exactly(self) -> None:
        calls: list[list[str]] = []

        def run(command, **_kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, '', '')

        with mock.patch.object(
            self.web, 'run', side_effect=run
        ), mock.patch.object(
            self.web, '_service_enablement_state',
            return_value='enabled-runtime',
        ), mock.patch.object(
            self.web, '_service_active', return_value=False
        ):
            errors = self.web._restore_service_state(
                'lsws.service', was_active=False,
                enablement_state='enabled-runtime',
            )

        self.assertEqual(errors, [])
        self.assertEqual(
            calls,
            [
                ['systemctl', 'disable', 'lsws.service'],
                ['systemctl', 'enable', '--runtime', 'lsws.service'],
                ['systemctl', 'stop', 'lsws.service'],
            ],
        )

    def test_activation_restores_runtime_mask_after_failure(self) -> None:
        calls: list[list[str]] = []
        enablement_queries = iter(
            ('masked-runtime', 'enabled', 'enabled', 'masked-runtime')
        )

        def run(command, **_kwargs):
            calls.append(command)
            if command[:2] == ['systemctl', 'is-enabled']:
                state = next(enablement_queries)
                return subprocess.CompletedProcess(
                    command,
                    1 if state == 'masked-runtime' else 0,
                    state + '\n',
                    '',
                )
            if command[:2] == ['systemctl', 'is-active']:
                return subprocess.CompletedProcess(
                    command, 3, 'inactive\n', ''
                )
            if command[:3] == ['systemctl', 'enable', '--now']:
                raise OSError('activation failed')
            return subprocess.CompletedProcess(command, 0, '', '')

        with mock.patch.object(self.web, 'run', side_effect=run):
            with self.assertRaisesRegex(OSError, 'activation failed'):
                self.web.activate_openlitespeed()

        self.assertEqual(
            calls,
            [
                ['systemctl', 'is-enabled', 'lsws.service'],
                ['systemctl', 'unmask', '--runtime', 'lsws.service'],
                ['systemctl', 'is-enabled', 'lsws.service'],
                ['systemctl', 'is-active', 'lsws.service'],
                ['systemctl', 'enable', '--now', 'lsws.service'],
                ['systemctl', 'enable', 'lsws.service'],
                ['systemctl', 'stop', 'lsws.service'],
                ['systemctl', 'is-enabled', 'lsws.service'],
                ['systemctl', 'is-active', 'lsws.service'],
                ['systemctl', 'mask', '--runtime', 'lsws.service'],
                ['systemctl', 'is-enabled', 'lsws.service'],
            ],
        )

    def test_persistent_mask_is_rejected_without_mutation(self) -> None:
        calls: list[list[str]] = []

        def run(command, **_kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 1, 'masked\n', '')

        with mock.patch.object(self.web, 'run', side_effect=run):
            with self.assertRaisesRegex(
                self.web.BuildError, 'persistently masked'
            ):
                self.web.activate_openlitespeed()

        self.assertEqual(
            calls, [['systemctl', 'is-enabled', 'lsws.service']]
        )

    def test_unsupported_enablement_state_is_rejected_before_activation(self) -> None:
        calls: list[list[str]] = []

        def run(command, **_kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, 'static\n', '')

        with mock.patch.object(self.web, 'run', side_effect=run):
            with self.assertRaisesRegex(
                self.web.BuildError, 'unsupported enablement state: static'
            ):
                self.web.activate_openlitespeed()

        self.assertEqual(
            calls, [['systemctl', 'is-enabled', 'lsws.service']]
        )

    def test_preparation_failure_uses_outer_snapshot(self) -> None:
        snapshots = [('path', pathlib.Path('/main'), ('file', 'old'))]
        with mock.patch.object(
            self.web, 'read_config', return_value={'webserver': 'openlitespeed'}
        ), mock.patch.object(
            self.web, 'managed_domains', return_value=[]
        ), mock.patch.object(
            self.web, '_preparation_snapshots', return_value=snapshots
        ), mock.patch.object(
            self.web, 'prepare_openlitespeed',
            side_effect=OSError('preparation fsync failed'),
        ), mock.patch.object(
            self.web, '_restore_preparation', return_value=[]
        ) as restore:
            with self.assertRaisesRegex(
                self.web.BuildError,
                'preparation failed and was rolled back: preparation fsync failed',
            ):
                self.web.main(['openlitespeed'])

        restore.assert_called_once_with(snapshots)

    def test_late_domain_failure_restores_preparation_snapshot(self) -> None:
        snapshots = [('path', pathlib.Path('/main'), ('file', 'old'))]
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
        snapshots = [('path', pathlib.Path('/main'), ('file', 'old'))]
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

    def test_snapshot_covers_every_file_and_directory_mutation(self) -> None:
        directory_paths = [
            pathlib.Path('/fcgi-bin'), pathlib.Path('/hostpanel'),
            pathlib.Path('/vhosts'), pathlib.Path('/state'),
            pathlib.Path('/state/domains'), pathlib.Path('/logs'),
        ]
        file_paths: list[tuple[pathlib.Path, bool]] = []

        def capture(path, *, allow_symlink=False):
            file_paths.append((path, allow_symlink))
            return ('missing',)

        with mock.patch.object(
            self.web, '_directory_snapshot_paths', return_value=directory_paths
        ), mock.patch.object(
            self.web, '_snapshot_directory', return_value=('missing',)
        ), mock.patch.object(
            self.web, '_snapshot_path', side_effect=capture
        ):
            snapshots = self.web._preparation_snapshots()

        self.assertEqual(len(snapshots), 11)
        self.assertEqual(
            [item[:2] for item in snapshots[:6]],
            [('directory', path) for path in directory_paths],
        )
        self.assertEqual(
            file_paths,
            [
                (self.web.OLS_MAIN, False),
                (self.web.OLS_ADMIN, False),
                (self.web.OLS_REGISTRY, False),
                (self.web.OLS_ROOT / 'fcgi-bin/lsphp', True),
                (self.web.LSPHP_STATE, False),
            ],
        )

    def test_new_directory_chain_is_removed_deepest_first(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            parent = root / 'created'
            child = parent / 'nested'
            child.mkdir(parents=True)
            snapshots = [
                ('directory', parent, ('missing',)),
                ('directory', child, ('missing',)),
            ]
            errors = self.web._restore_preparation(snapshots)
            self.assertEqual(errors, [])
            self.assertFalse(parent.exists())

    def test_existing_directory_metadata_snapshot_is_reapplied(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / 'managed'
            path.mkdir(mode=0o755)
            snapshot = (
                'directory', 0o750, os.getuid(), os.getgid(),
                {'user.hostpanel-test': b'original'},
            )
            with mock.patch.object(
                self.web, '_apply_xattrs'
            ) as apply_xattrs, mock.patch.object(
                self.web, '_fsync_directory'
            ) as fsync_directory:
                self.web._restore_directory(path, snapshot)
            self.assertEqual(path.stat().st_mode & 0o777, 0o750)
            apply_xattrs.assert_called_once_with(
                path, {'user.hostpanel-test': b'original'}
            )
            fsync_directory.assert_called_once_with(path)

    def test_installed_layout_contains_preserved_implementation(self) -> None:
        source = INSTALLER.read_text(encoding='utf-8')
        self.assertIn('hostpanel_build_web_impl.py', source)
        self.assertIn("'_hostpanel_build_web_impl'", WRAPPER.read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
