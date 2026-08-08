from __future__ import annotations

import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import hostpanel_build_entry as entry
from hostpanel_build_config import DEFAULT_OPTIONS, Platform


class AllOptionalReconciliationTests(unittest.TestCase):
    platform = Platform('debian', 'ubuntu', '24.04')

    def arguments(self, options, roles):
        return (
            'all', options, self.platform,
            pathlib.Path('/log'), pathlib.Path('/backup'),
            pathlib.Path('/python'), pathlib.Path('/doctor'), roles,
            pathlib.Path('/web-helper'), pathlib.Path('/web-mode'),
        )

    def test_all_plan_includes_off_actions_for_role_components(self) -> None:
        options = dict(DEFAULT_OPTIONS)
        components = entry._all_role_components(
            'all', options, {'database', 'web'}
        )
        self.assertIn('mongodb', components)
        self.assertIn('varnish', components)
        self.assertEqual(components.count('mongodb'), 1)
        self.assertEqual(components.count('varnish'), 1)

    def test_all_database_reconciles_mongodb_off_after_core_build(self) -> None:
        options = dict(DEFAULT_OPTIONS)
        events: list[str] = []
        with mock.patch.object(
            entry, '_ORIGINAL_BASE_EXECUTE_BUILD',
            side_effect=lambda *_args: events.append('core'),
        ), mock.patch.object(
            entry.cli, 'apply_mongodb',
            side_effect=lambda *_args: events.append('mongodb-off'),
        ) as mongodb, mock.patch.object(
            entry.cli, 'run_doctor',
            side_effect=lambda *_args: events.append('doctor'),
        ) as doctor:
            entry._execute_all_optional_reconciliation(
                *self.arguments(options, {'database'})
            )

        self.assertEqual(events, ['core', 'mongodb-off', 'doctor'])
        mongodb.assert_called_once()
        doctor.assert_called_once()

    def test_all_does_not_duplicate_enabled_mongodb_transaction(self) -> None:
        options = dict(DEFAULT_OPTIONS)
        options['mongodb'] = '8.0'
        with mock.patch.object(
            entry, '_ORIGINAL_BASE_EXECUTE_BUILD'
        ) as core, mock.patch.object(
            entry.cli, 'apply_mongodb'
        ) as mongodb, mock.patch.object(
            entry.cli, 'run_doctor'
        ) as doctor:
            entry._execute_all_optional_reconciliation(
                *self.arguments(options, {'database'})
            )

        core.assert_called_once()
        mongodb.assert_not_called()
        doctor.assert_not_called()

    def test_non_all_build_does_not_add_mongodb_reconciliation(self) -> None:
        options = dict(DEFAULT_OPTIONS)
        args = list(self.arguments(options, {'database'}))
        args[0] = 'database'
        with mock.patch.object(
            entry, '_ORIGINAL_BASE_EXECUTE_BUILD'
        ) as core, mock.patch.object(
            entry.cli, 'apply_mongodb'
        ) as mongodb, mock.patch.object(
            entry.cli, 'run_doctor'
        ) as doctor:
            entry._execute_all_optional_reconciliation(*args)

        core.assert_called_once()
        mongodb.assert_not_called()
        doctor.assert_not_called()


if __name__ == '__main__':
    unittest.main()
