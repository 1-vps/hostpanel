from __future__ import annotations

import os
import pathlib
import stat
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import hostpanel_build_web_transaction_adapter as transaction
from hostpanel_build_config import BuildError, Platform


class OuterWebBuildTransactionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_reconcile = transaction.operations.reconcile_webserver
        self.platform = Platform('debian', 'ubuntu', '24.04')
        self.log = pathlib.Path('/tmp/hostpanel-build.log')
        self.backup = pathlib.Path('/tmp/hostpanel-backup')
        self.python = pathlib.Path('/opt/hostpanel/venv/bin/python')
        self.doctor = pathlib.Path('/opt/hostpanel/app/hostpanel-doctor')
        self.web_helper = pathlib.Path('/opt/hostpanel/tools/hostpanel-build-web')
        self.mode_file = pathlib.Path('/etc/hostpanel/webserver-mode')
        self.service_state = transaction.ServiceState(False, 'disabled')

    def tearDown(self) -> None:
        transaction.operations.reconcile_webserver = self.original_reconcile

    def invoke(self, function, *, component='web', roles=None):
        return transaction.guarded_execute_build(
            function,
            component,
            {'webserver': 'openlitespeed'},
            self.platform,
            self.log,
            self.backup,
            self.python,
            self.doctor,
            {'web'} if roles is None else roles,
            self.web_helper,
            self.mode_file,
        )

    def common_patches(self, state_path: pathlib.Path):
        return (
            mock.patch.object(transaction, '_trusted_executable'),
            mock.patch.object(transaction, '_trusted_tool'),
            mock.patch.object(
                transaction, '_state_helper',
                return_value=pathlib.Path('/opt/hostpanel/tools/hostpanel_build_web_state.py'),
            ),
            mock.patch.object(
                transaction, '_transaction_state_path', return_value=state_path
            ),
            mock.patch.object(
                transaction, '_capture_service', return_value=self.service_state
            ),
        )

    def test_non_web_build_is_passed_through_without_transaction_state(self) -> None:
        function = mock.Mock(return_value='ok')
        result = self.invoke(function, component='dns', roles={'dns'})
        self.assertEqual(result, 'ok')
        function.assert_called_once()

    def test_capture_occurs_after_package_stage_and_before_reconcile(self) -> None:
        events: list[str] = []
        with tempfile.TemporaryDirectory() as directory:
            state_path = pathlib.Path(directory) / 'state.json'

            def helper_command(
                _python, _helper, action, path, _log, *, mode_file=None
            ):
                events.append(action)
                if action == '--capture':
                    self.assertEqual(mode_file, self.mode_file)
                    path.write_text('{}\n', encoding='utf-8')
                    path.chmod(0o600)

            def base_reconcile(*_args):
                events.append('base-reconcile')

            def function(*args):
                events.append('package-stage')
                transaction.operations.reconcile_webserver(
                    args[1], args[8], args[5], args[9], args[3]
                )
                events.append('late-stage')
                return 'ok'

            transaction.operations.reconcile_webserver = base_reconcile
            patches = self.common_patches(state_path)
            with patches[0], patches[1], patches[2], patches[3], patches[4], \
                 mock.patch.object(
                     transaction, '_helper_command', side_effect=helper_command
                 ), mock.patch.object(
                     transaction, '_discard_state',
                     side_effect=lambda path: (
                         events.append('discard'), path.unlink(missing_ok=True)
                     ),
                 ), mock.patch.object(transaction, '_restore_service') as restore:
                result = self.invoke(function)

            self.assertEqual(result, 'ok')
            self.assertEqual(
                events,
                [
                    'package-stage', '--capture', '--verify',
                    'base-reconcile', 'late-stage', 'discard',
                ],
            )
            restore.assert_not_called()
            self.assertFalse(state_path.exists())
            self.assertIs(
                transaction.operations.reconcile_webserver, base_reconcile
            )

    def test_late_failure_restores_helper_then_services_in_reverse_order(self) -> None:
        events: list[str] = []
        with tempfile.TemporaryDirectory() as directory:
            state_path = pathlib.Path(directory) / 'state.json'

            def helper_command(
                _python, _helper, action, path, _log, *, mode_file=None
            ):
                events.append(action)
                if action == '--capture':
                    path.write_text('{}\n', encoding='utf-8')
                    path.chmod(0o600)

            def base_reconcile(*_args):
                events.append('base-reconcile')

            def function(*args):
                transaction.operations.reconcile_webserver(
                    args[1], args[8], args[5], args[9], args[3]
                )
                events.append('doctor')
                raise BuildError('late doctor failed')

            def restore_service(name, _state, _log):
                events.append(f'restore:{name}')
                return []

            transaction.operations.reconcile_webserver = base_reconcile
            patches = self.common_patches(state_path)
            with patches[0], patches[1], patches[2], patches[3], patches[4], \
                 mock.patch.object(
                     transaction, '_helper_command', side_effect=helper_command
                 ), mock.patch.object(
                     transaction, '_restore_service', side_effect=restore_service
                 ), mock.patch.object(
                     transaction, '_discard_state',
                     side_effect=lambda path: (
                         events.append('discard'), path.unlink(missing_ok=True)
                     ),
                 ):
                with self.assertRaisesRegex(BuildError, 'late doctor failed'):
                    self.invoke(function)

            self.assertEqual(
                events,
                [
                    '--capture', '--verify', 'base-reconcile', 'doctor',
                    '--restore', 'restore:lsws.service',
                    'restore:apache2.service', 'restore:nginx.service',
                    'discard',
                ],
            )
            self.assertFalse(state_path.exists())
            self.assertIs(
                transaction.operations.reconcile_webserver, base_reconcile
            )

    def test_failure_before_reconcile_restores_only_services(self) -> None:
        events: list[str] = []
        with tempfile.TemporaryDirectory() as directory:
            state_path = pathlib.Path(directory) / 'state.json'

            def function(*_args):
                raise OSError('package transaction failed')

            def restore_service(name, _state, _log):
                events.append(name)
                return []

            patches = self.common_patches(state_path)
            with patches[0], patches[1], patches[2], patches[3], patches[4], \
                 mock.patch.object(
                     transaction, '_helper_command'
                 ) as helper, mock.patch.object(
                     transaction, '_restore_service', side_effect=restore_service
                 ), mock.patch.object(transaction, '_discard_state') as discard:
                with self.assertRaisesRegex(OSError, 'package transaction failed'):
                    self.invoke(function)

            helper.assert_not_called()
            discard.assert_called_once_with(state_path)
            self.assertEqual(
                events,
                ['lsws.service', 'apache2.service', 'nginx.service'],
            )

    def test_rollback_errors_are_aggregated_and_state_is_retained(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = pathlib.Path(directory) / 'state.json'

            def helper_command(
                _python, _helper, action, path, _log, *, mode_file=None
            ):
                if action == '--capture':
                    path.write_text('{}\n', encoding='utf-8')
                    path.chmod(0o600)
                elif action == '--restore':
                    raise BuildError('state restore failed')

            def function(*args):
                transaction.operations.reconcile_webserver(
                    args[1], args[8], args[5], args[9], args[3]
                )
                raise BuildError('late failure')

            transaction.operations.reconcile_webserver = lambda *_args: None
            patches = self.common_patches(state_path)
            with patches[0], patches[1], patches[2], patches[3], patches[4], \
                 mock.patch.object(
                     transaction, '_helper_command', side_effect=helper_command
                 ), mock.patch.object(
                     transaction, '_restore_service',
                     side_effect=[['lsws rollback failed'], [], []],
                 ), mock.patch.object(transaction, '_discard_state') as discard:
                with self.assertRaises(transaction.BuildError) as raised:
                    self.invoke(function)

            message = str(raised.exception)
            self.assertIn('late failure', message)
            self.assertIn('state restore failed', message)
            self.assertIn('lsws rollback failed', message)
            self.assertIn(str(state_path), message)
            self.assertTrue(state_path.exists())
            discard.assert_not_called()

    def test_service_capture_rejects_enablement_race(self) -> None:
        with mock.patch.object(
            transaction, '_service_enablement',
            side_effect=['enabled', 'disabled'],
        ), mock.patch.object(
            transaction, '_service_active', return_value=True
        ):
            with self.assertRaisesRegex(
                BuildError, 'enablement changed while its pre-build state was captured'
            ):
                transaction._capture_service('nginx.service')

    def test_virtualenv_python_symlink_resolves_to_trusted_executable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            target = root / 'python3'
            target.write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')
            target.chmod(0o755)
            link = root / 'python'
            link.symlink_to(target.name)
            with mock.patch.object(transaction, '_trusted_directory_chain'):
                resolved = transaction._trusted_executable(link)
            self.assertEqual(resolved, target)

    def test_entry_wraps_powerdns_with_the_outer_web_guard(self) -> None:
        source = (TOOLS / 'hostpanel_build_entry.py').read_text(encoding='utf-8')
        outer = source.index('web_transaction_adapter.guarded_execute_build(')
        inner = source.index('powerdns_adapter.guarded_apply_build(')
        self.assertLess(inner, outer)
        self.assertIn('powerdns_guarded_execute', source[inner:outer])

    def test_source_files_are_installed_and_ci_tracked(self) -> None:
        installer = (TOOLS / 'install-hostpanel-build.sh').read_text(
            encoding='utf-8'
        )
        workflow = (
            ROOT / '.github/workflows/automatic-installer.yml'
        ).read_text(encoding='utf-8')
        for name in (
            'hostpanel_build_web_state.py',
            'hostpanel_build_web_transaction_adapter.py',
        ):
            self.assertIn(name, installer)
            self.assertGreaterEqual(workflow.count(f'tools/{name}'), 3)


if __name__ == '__main__':
    unittest.main()
