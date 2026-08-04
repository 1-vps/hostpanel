from __future__ import annotations

import pathlib
import subprocess
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
sys.path.insert(0, str(TOOLS))

import hostpanel_build_extras_state as STATE
from hostpanel_build_config import BuildError


class OptionalServiceStateWrapperTests(unittest.TestCase):
    def test_active_probe_requires_explicit_state_text(self):
        cases = (
            (subprocess.CompletedProcess([], 0, 'active\n', ''), True),
            (subprocess.CompletedProcess([], 3, 'inactive\n', ''), False),
            (subprocess.CompletedProcess([], 3, 'failed\n', ''), False),
        )
        for completed, expected in cases:
            with self.subTest(completed=completed), mock.patch.object(
                STATE, 'run_command', return_value=completed
            ):
                self.assertIs(STATE._service_active('unit.service'), expected)
        with mock.patch.object(
            STATE, 'run_command', return_value=subprocess.CompletedProcess(
                [], 0, '', ''
            )
        ), self.assertRaisesRegex(BuildError, 'could not determine active state'):
            STATE._service_active('unit.service')
        with mock.patch.object(
            STATE, 'run_command', return_value=subprocess.CompletedProcess(
                [], 1, '', 'Failed to connect to bus'
            )
        ), self.assertRaisesRegex(BuildError, 'Failed to connect to bus'):
            STATE._service_active('unit.service')

    def test_enabled_probe_classifies_exact_unit_file_state(self):
        cases = (
            (subprocess.CompletedProcess([], 0, 'enabled\n', ''), True),
            (subprocess.CompletedProcess([], 0, 'static\n', ''), False),
            (subprocess.CompletedProcess([], 1, 'disabled\n', ''), False),
            (subprocess.CompletedProcess([], 1, 'masked\n', ''), False),
        )
        for completed, expected in cases:
            with self.subTest(completed=completed), mock.patch.object(
                STATE, 'run_command', return_value=completed
            ):
                self.assertIs(STATE._service_enabled('unit.service'), expected)
        with mock.patch.object(
            STATE, 'run_command', return_value=subprocess.CompletedProcess(
                [], 0, '', ''
            )
        ), self.assertRaisesRegex(BuildError, 'could not determine enabled state'):
            STATE._service_enabled('unit.service')

    def test_failed_candidate_stop_blocks_all_later_rollback_mutations(self):
        errors: list[str] = []
        mutations: list[str] = []

        def stop_failure() -> None:
            raise BuildError('could not stop candidate')

        self.assertFalse(STATE._attempt(
            errors, 'candidate MongoDB stop', stop_failure
        ))
        self.assertFalse(STATE._attempt(
            errors, 'MongoDB configuration restore', mutations.append, 'config'
        ))
        self.assertFalse(STATE._attempt(
            errors, 'MongoDB mode restore', mutations.append, 'mode'
        ))
        self.assertFalse(STATE._attempt(
            errors, 'MongoDB service restore', mutations.append, 'service'
        ))
        self.assertEqual(mutations, [])
        with self.assertRaisesRegex(
            BuildError, 'rollback also failed.*could not stop candidate'
        ):
            STATE._finish_rollback(
                'MongoDB enable', BuildError('original failure'), errors
            )
        self.assertNotIn(id(errors), STATE._ROLLBACK_GUARDS)

    def test_successful_candidate_stop_allows_rollback_mutations(self):
        errors: list[str] = []
        mutations: list[str] = []
        self.assertTrue(STATE._attempt(
            errors, 'candidate Varnish stop', lambda: None
        ))
        self.assertTrue(STATE._attempt(
            errors, 'Varnish VCL restore', mutations.append, 'vcl'
        ))
        STATE._finish_rollback(
            'Varnish enable', BuildError('original failure'), errors
        )
        self.assertEqual(mutations, ['vcl'])
        self.assertNotIn(id(errors), STATE._ROLLBACK_GUARDS)


if __name__ == '__main__':
    unittest.main()
