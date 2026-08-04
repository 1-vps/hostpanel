from __future__ import annotations

import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import hostpanel_build_optional_postcheck_adapter as postchecks
from hostpanel_build_config import Platform


class MixedOptionalPostcheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mongodb = postchecks.cli.apply_mongodb
        self.varnish = postchecks.cli.apply_varnish
        self.doctor = postchecks.cli.run_doctor
        self.args = (
            'all', {}, Platform('debian', 'ubuntu', '24.04'),
            pathlib.Path('/log'), pathlib.Path('/backup'),
            pathlib.Path('/python'), pathlib.Path('/doctor'),
            {'web', 'database'}, pathlib.Path('/web'), pathlib.Path('/mode'),
        )

    def tearDown(self) -> None:
        postchecks.cli.apply_mongodb = self.mongodb
        postchecks.cli.apply_varnish = self.varnish
        postchecks.cli.run_doctor = self.doctor

    def test_transactional_and_legacy_mix_keeps_final_doctor(self) -> None:
        events: list[str] = []

        def mongodb(_options, _platform, _log, _backup, *, post_apply=None):
            events.append('mongodb')
            if post_apply is not None:
                post_apply()

        mongodb._hostpanel_transactional_post_apply = True  # type: ignore[attr-defined]

        def varnish(_options, _platform, _log, _backup):
            events.append('varnish')

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
            events, ['mongodb', 'doctor', 'varnish', 'doctor']
        )
        self.assertEqual(doctor.call_count, 2)

    def test_installer_and_workflow_track_optional_adapter(self) -> None:
        name = 'hostpanel_build_optional_postcheck_adapter.py'
        installer = (TOOLS / 'install-hostpanel-build.sh').read_text(
            encoding='utf-8'
        )
        workflow = (
            ROOT / '.github/workflows/automatic-installer.yml'
        ).read_text(encoding='utf-8')
        self.assertEqual(installer.count(f'  {name}\n'), 1)
        self.assertGreaterEqual(workflow.count(f'tools/{name}'), 3)


if __name__ == '__main__':
    unittest.main()
