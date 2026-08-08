from __future__ import annotations

import importlib.util
import pathlib
import subprocess
import sys
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
WEB_PATH = ROOT / 'tools/hostpanel_build_web.py'


def load_web(name: str):
    saved = {
        key: sys.modules.get(key)
        for key in ('store', 'modules', '_hostpanel_build_web_impl')
    }
    store = types.ModuleType('store')
    store.connect = lambda: None
    modules = types.ModuleType('modules')
    modules.webserver = types.SimpleNamespace()
    sys.modules['store'] = store
    sys.modules['modules'] = modules
    sys.modules.pop('_hostpanel_build_web_impl', None)
    try:
        spec = importlib.util.spec_from_file_location(name, WEB_PATH)
        if spec is None or spec.loader is None:
            raise RuntimeError('could not load OpenLiteSpeed wrapper')
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        for key, value in saved.items():
            if value is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = value


class OpenLiteSpeedDirectoryDurabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.web = load_web('_hostpanel_web_directory_durability_test')

    @classmethod
    def tearDownClass(cls) -> None:
        sys.modules.pop('_hostpanel_web_directory_durability_test', None)

    def test_enabled_state_requires_success_return_code(self) -> None:
        completed = subprocess.CompletedProcess(
            ['systemctl', 'is-enabled', 'lsws.service'],
            1,
            'enabled\n',
            '',
        )
        with mock.patch.object(self.web, 'run', return_value=completed):
            with self.assertRaisesRegex(
                self.web.BuildError,
                'could not determine enabled state',
            ):
                self.web._service_enablement_state('lsws.service')

    def test_directory_barrier_fsyncs_inode_and_parent_in_order(self) -> None:
        first = pathlib.Path('/first')
        second = pathlib.Path('/second')
        snapshots = [
            ('directory', first, ('missing',)),
            ('path', pathlib.Path('/config'), ('missing',)),
            ('directory', second, ('missing',)),
        ]
        events: list[tuple[str, pathlib.Path]] = []
        with mock.patch.object(
            self.web,
            '_fsync_directory',
            side_effect=lambda path: events.append(('directory', path)),
        ), mock.patch.object(
            self.web,
            '_fsync_parent',
            side_effect=lambda path: events.append(('parent', path)),
        ):
            self.web._fsync_preparation_directories(snapshots)

        self.assertEqual(
            events,
            [
                ('directory', first), ('parent', first),
                ('directory', second), ('parent', second),
            ],
        )

    def test_directory_fsync_failure_restores_outer_snapshot(self) -> None:
        snapshots = [
            ('directory', pathlib.Path('/created'), ('missing',))
        ]
        with mock.patch.object(
            self.web, 'read_config', return_value={'webserver': 'openlitespeed'}
        ), mock.patch.object(
            self.web, 'managed_domains', return_value=[]
        ), mock.patch.object(
            self.web, '_preparation_snapshots', return_value=snapshots
        ), mock.patch.object(
            self.web, 'prepare_openlitespeed'
        ) as prepare, mock.patch.object(
            self.web, '_fsync_preparation_directories',
            side_effect=OSError('directory fsync failed'),
        ), mock.patch.object(
            self.web, '_restore_preparation', return_value=[]
        ) as restore:
            with self.assertRaisesRegex(
                self.web.BuildError,
                'preparation failed and was rolled back: directory fsync failed',
            ):
                self.web.main(['openlitespeed'])

        prepare.assert_called_once()
        restore.assert_called_once_with(snapshots)

    def test_source_places_directory_barrier_before_domain_switch(self) -> None:
        source = WEB_PATH.read_text(encoding='utf-8')
        main = source.split('def main(', 1)[1]
        prepare = main.index('prepare_openlitespeed(options)')
        barrier = main.index('_fsync_preparation_directories(preparation)', prepare)
        switch = main.index('webserver.set_mode(domain, target, admin)', barrier)
        self.assertLess(prepare, barrier)
        self.assertLess(barrier, switch)


if __name__ == '__main__':
    unittest.main()
