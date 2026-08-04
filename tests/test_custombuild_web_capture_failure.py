from __future__ import annotations

import pathlib
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


class WebCaptureFailureTests(unittest.TestCase):
    def test_capture_failure_does_not_restore_unmodified_services(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = pathlib.Path(directory) / 'web-state.json'
            function = mock.Mock()
            service_state = transaction.ServiceState(False, 'disabled')
            original_reconcile = transaction.operations.reconcile_webserver
            with mock.patch.object(transaction, '_trusted_executable'), \
                 mock.patch.object(transaction, '_trusted_tool'), \
                 mock.patch.object(
                     transaction, '_state_helper',
                     return_value=pathlib.Path(directory) / 'state-helper.py',
                 ), mock.patch.object(
                     transaction, '_transaction_state_path',
                     return_value=state_path,
                 ), mock.patch.object(
                     transaction, '_capture_service',
                     return_value=service_state,
                 ), mock.patch.object(
                     transaction, '_helper_command',
                     side_effect=BuildError('capture failed'),
                 ), mock.patch.object(
                     transaction, '_restore_service'
                 ) as restore, mock.patch.object(
                     transaction, '_discard_state'
                 ) as discard:
                with self.assertRaisesRegex(BuildError, 'capture failed'):
                    transaction.guarded_execute_build(
                        function,
                        'web',
                        {'webserver': 'openlitespeed'},
                        Platform('debian', 'ubuntu', '24.04'),
                        pathlib.Path('/log'),
                        pathlib.Path('/backup'),
                        pathlib.Path('/python'),
                        pathlib.Path('/doctor'),
                        {'web'},
                        pathlib.Path('/web'),
                        pathlib.Path('/mode'),
                    )
            function.assert_not_called()
            restore.assert_not_called()
            discard.assert_called_once_with(state_path)
            self.assertIs(
                transaction.operations.reconcile_webserver,
                original_reconcile,
            )


if __name__ == '__main__':
    unittest.main()
