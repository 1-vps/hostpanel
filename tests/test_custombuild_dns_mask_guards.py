from __future__ import annotations

import pathlib
import subprocess
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import hostpanel_build_powerdns_adapter as adapter
from hostpanel_build_config import BuildError


class DnsMaskGuardTests(unittest.TestCase):
    def test_masked_unit_file_states_are_rejected(self) -> None:
        for state in ('masked', 'masked-runtime'):
            completed = subprocess.CompletedProcess(
                [], 1, state + '\n', ''
            )
            with self.subTest(state=state), mock.patch.object(
                adapter._IMPL, 'run_command', return_value=completed
            ), self.assertRaisesRegex(
                BuildError, 'refusing to remove an administrative mask'
            ):
                adapter.service_enabled('pdns.service')

    def test_regular_disabled_state_remains_supported(self) -> None:
        completed = subprocess.CompletedProcess([], 1, 'disabled\n', '')
        with mock.patch.object(
            adapter._IMPL, 'run_command', return_value=completed
        ):
            self.assertIs(adapter.service_enabled('pdns.service'), False)

    def test_runtime_only_enablement_is_rejected_before_mutation(self) -> None:
        completed = subprocess.CompletedProcess(
            [], 0, 'enabled-runtime\n', ''
        )
        with mock.patch.object(
            adapter._IMPL, 'run_command', return_value=completed
        ), self.assertRaisesRegex(
            BuildError, 'could not determine enabled state'
        ):
            adapter.service_enabled('pdns.service')


if __name__ == '__main__':
    unittest.main()
