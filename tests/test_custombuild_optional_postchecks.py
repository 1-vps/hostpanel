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
import hostpanel_build_extras_state as state
import hostpanel_build_optional_postcheck_adapter as postchecks
from hostpanel_build_config import BuildError, Platform


class OptionalPostcheckAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_mongodb = postchecks.cli.apply_mongodb
        self.original_varnish = postchecks.cli.apply_varnish
        self.original_doctor = postchecks.cli.run_doctor
        self.args = (
            'web',
            {'webserver': 'openlitespeed', 'varnish': 'on'},
            Platform('debian', 'ubuntu', '24.04'),
            pathlib.Path('/tmp/build.log'),
            pathlib.Path('/tmp/backups'),
            pathlib.Path('/opt/hostpanel/venv/bin/python'),
            pathlib.Path('/opt/hostpanel/app/hostpanel-doctor'),
            {'web'},
            pathlib.Path('/opt/hostpanel/tools/hostpanel-build-web'),
            pathlib.Path('/etc/hostpanel/webserver-mode'),
        )

    def tearDown(self) -> None:
        postchecks.cli.apply_mongodb = self.original_mongodb
        postchecks.cli.apply_varnish = self.original_varnish
        postchecks.cli.run_doctor = self.original_doctor

    @staticmethod
    def transactional_optional(events: list[str], label: str):
        def apply(_options, _platform, _log, _backup, *, post_apply=None):
            events.append(f'{label}:apply')
            try:
                if post_apply is not None:
                    post_apply()
            except Exception:
                events.append(f'{label}:rollback')
                raise
            events.append(f'{label}:commit')

        apply._hostpanel_transactional_post_apply = True  # type: ignore[attr-defined]
        return apply

    def test_transactional_varnish_runs_doctor_inside_component_boundary(self) -> None:
        events: list[str] = []
        varnish = self.transactional_optional(events, 'varnish')
        doctor = mock.Mock(side_effect=lambda *_args: events.append('doctor'))
        postchecks.cli.apply_varnish = varnish
        postchecks.cli.run_doctor = doctor

        def execute(*args):
            postchecks.cli.apply_varnish(args[1], args[2], args[3], args[4])
            postchecks.cli.run_doctor(args[3], args[5], args[6])
            events.append('execute:return')
            return 'ok'

        result = postchecks.guarded_execute_build(execute, *self.args)
        self.assertEqual(result, 'ok')
        self.assertEqual(
            events,
            ['varnish:apply', 'doctor', 'varnish:commit', 'execute:return'],
        )
        doctor.assert_called_once()
        self.assertIs(postchecks.cli.apply_varnish, varnish)
        self.assertIs(postchecks.cli.run_doctor, doctor)

    def test_doctor_failure_triggers_optional_component_rollback(self) -> None:
        events: list[str] = []
        varnish = self.transactional_optional(events, 'varnish')
        doctor = mock.Mock(side_effect=BuildError('doctor failed'))
        postchecks.cli.apply_varnish = varnish
        postchecks.cli.run_doctor = doctor

        def execute(*args):
            postchecks.cli.apply_varnish(args[1], args[2], args[3], args[4])
            self.fail('optional apply should have raised')

        with self.assertRaisesRegex(BuildError, 'doctor failed'):
            postchecks.guarded_execute_build(execute, *self.args)
        self.assertEqual(
            events, ['varnish:apply', 'varnish:rollback']
        )
        self.assertIs(postchecks.cli.apply_varnish, varnish)
        self.assertIs(postchecks.cli.run_doctor, doctor)

    def test_two_transactional_components_each_receive_their_own_postcheck(self) -> None:
        events: list[str] = []
        mongodb = self.transactional_optional(events, 'mongodb')
        varnish = self.transactional_optional(events, 'varnish')
        doctor = mock.Mock(side_effect=lambda *_args: events.append('doctor'))
        postchecks.cli.apply_mongodb = mongodb
        postchecks.cli.apply_varnish = varnish
        postchecks.cli.run_doctor = doctor

        def execute(*args):
            postchecks.cli.apply_mongodb(args[1], args[2], args[3], args[4])
            postchecks.cli.apply_varnish(args[1], args[2], args[3], args[4])
            postchecks.cli.run_doctor(args[3], args[5], args[6])
            return 'ok'

        self.assertEqual(
            postchecks.guarded_execute_build(execute, *self.args), 'ok'
        )
        self.assertEqual(
            events,
            [
                'mongodb:apply', 'doctor', 'mongodb:commit',
                'varnish:apply', 'doctor', 'varnish:commit',
            ],
        )
        self.assertEqual(doctor.call_count, 2)

    def test_non_transactional_optional_keeps_legacy_final_doctor(self) -> None:
        events: list[str] = []

        def varnish(_options, _platform, _log, _backup):
            events.append('varnish')

        doctor = mock.Mock(side_effect=lambda *_args: events.append('doctor'))
        postchecks.cli.apply_varnish = varnish
        postchecks.cli.run_doctor = doctor

        def execute(*args):
            postchecks.cli.apply_varnish(args[1], args[2], args[3], args[4])
            postchecks.cli.run_doctor(args[3], args[5], args[6])
            return 'legacy'

        self.assertEqual(
            postchecks.guarded_execute_build(execute, *self.args), 'legacy'
        )
        self.assertEqual(events, ['varnish', 'doctor'])
        doctor.assert_called_once()

    def test_rollback_wrapper_preserves_postcheck_capability_marker(self) -> None:
        function = mock.Mock()
        function._hostpanel_transactional_post_apply = True
        wrapped = entry.guarded_optional_apply(function)
        self.assertTrue(
            getattr(wrapped, '_hostpanel_transactional_post_apply', False)
        )

    def test_entry_composition_places_optional_guard_inside_powerdns_and_web(self) -> None:
        source = (TOOLS / 'hostpanel_build_entry.py').read_text(encoding='utf-8')
        optional_definition = source.index('def optional_guarded_execute(')
        optional_call = source.index(
            'optional_postcheck_adapter.guarded_execute_build(',
            optional_definition,
        )
        powerdns_definition = source.index(
            'def powerdns_guarded_execute(', optional_call
        )
        powerdns_call = source.index(
            'powerdns_adapter.guarded_apply_build(', powerdns_definition
        )
        web_call = source.index(
            'web_transaction_adapter.guarded_execute_build(', powerdns_call
        )
        self.assertLess(optional_definition, optional_call)
        self.assertLess(optional_call, powerdns_definition)
        self.assertLess(powerdns_definition, powerdns_call)
        self.assertLess(powerdns_call, web_call)


class OptionalStatePostValidationTests(unittest.TestCase):
    def test_postcheck_exception_is_seen_inside_varnish_transaction(self) -> None:
        events: list[str] = []
        original_validator = state._IMPL.validate_varnish

        def validator(*_args, **_kwargs):
            events.append('validate')

        def apply(*args):
            try:
                state._IMPL.validate_varnish(args[0], args[2])
            except Exception:
                events.append('rollback')
                raise
            events.append('commit')

        def post_apply():
            events.append('doctor')
            raise BuildError('postcheck failed')

        with mock.patch.object(state, '_ORIGINAL_APPLY_VARNISH', apply), \
             mock.patch.object(state._IMPL, 'validate_varnish', validator):
            with self.assertRaisesRegex(BuildError, 'postcheck failed'):
                state.apply_varnish(
                    {}, mock.Mock(), pathlib.Path('/log'), pathlib.Path('/backup'),
                    post_apply=post_apply,
                )
            self.assertIs(state._IMPL.validate_varnish, validator)

        self.assertEqual(events, ['validate', 'doctor', 'rollback'])
        state._IMPL.validate_varnish = original_validator

    def test_successful_postcheck_completes_before_component_commit(self) -> None:
        events: list[str] = []

        def validator(*_args, **_kwargs):
            events.append('validate')

        def apply(*args):
            state._IMPL.validate_mongodb(args[0], args[2])
            events.append('commit')

        with mock.patch.object(state, '_ORIGINAL_APPLY_MONGODB', apply), \
             mock.patch.object(state._IMPL, 'validate_mongodb', validator):
            state.apply_mongodb(
                {}, mock.Mock(), pathlib.Path('/log'), pathlib.Path('/backup'),
                post_apply=lambda: events.append('doctor'),
            )

        self.assertEqual(events, ['validate', 'doctor', 'commit'])


if __name__ == '__main__':
    unittest.main()
