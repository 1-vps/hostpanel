from __future__ import annotations

import importlib.util
import pathlib
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
STATE_PATH = TOOLS / 'hostpanel_build_web_state.py'


def load_state_module(name: str):
    saved_path = list(sys.path)
    isolated = (
        'store', 'modules', '_hostpanel_build_web_impl',
        '_hostpanel_build_web_state_runtime',
    )
    saved_modules = {key: sys.modules.get(key) for key in isolated}
    store = types.ModuleType('store')
    store.connect = lambda: None
    modules = types.ModuleType('modules')
    modules.webserver = types.SimpleNamespace()
    sys.modules['store'] = store
    sys.modules['modules'] = modules
    sys.path.insert(0, str(TOOLS))
    try:
        spec = importlib.util.spec_from_file_location(name, STATE_PATH)
        if spec is None or spec.loader is None:
            raise RuntimeError('could not load web state helper')
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path[:] = saved_path
        for key, value in saved_modules.items():
            if value is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = value


class WebTransactionStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.state = load_state_module('_hostpanel_web_state_test')

    @classmethod
    def tearDownClass(cls) -> None:
        sys.modules.pop('_hostpanel_web_state_test', None)

    def test_snapshot_round_trip_preserves_file_directory_and_xattrs(self) -> None:
        snapshots = (
            ('missing',),
            ('symlink', '../lsphp85/bin/lsphp'),
            (
                'file', 'value\n', 0o640, 0, 7,
                {'security.selinux': b'context', 'user.test': b'\x00\xff'},
            ),
            (
                'directory', 0o750, 0, 7,
                {'system.posix_acl_access': b'acl'},
            ),
        )
        for snapshot in snapshots:
            encoded = self.state._encode_snapshot(snapshot)
            decoded = self.state._decode_snapshot(encoded)
            self.assertEqual(decoded, snapshot)

    def test_state_shape_rejects_duplicate_paths(self) -> None:
        entry = {
            'entry_kind': 'path',
            'path': '/etc/hostpanel/webserver-mode',
            'snapshot': {'kind': 'missing'},
        }
        payload = {
            'schema': 1,
            'mode_file': '/etc/hostpanel/webserver-mode',
            'domains': {},
            'snapshots': [entry, dict(entry)],
        }
        with self.assertRaisesRegex(
            self.state.BuildError, 'duplicate web transaction path'
        ):
            self.state._validate_state_shape(payload)

    def test_state_shape_rejects_directory_entry_with_file_snapshot(self) -> None:
        payload = {
            'schema': 1,
            'mode_file': '/etc/hostpanel/webserver-mode',
            'domains': {},
            'snapshots': [
                {
                    'entry_kind': 'directory',
                    'path': '/usr/local/lsws/conf/hostpanel',
                    'snapshot': {
                        'kind': 'file', 'text': '', 'mode': 0o640,
                        'uid': 0, 'gid': 0, 'xattrs': {},
                    },
                },
                {
                    'entry_kind': 'path',
                    'path': '/etc/hostpanel/webserver-mode',
                    'snapshot': {'kind': 'missing'},
                },
            ],
        }
        with self.assertRaisesRegex(
            self.state.BuildError, 'non-directory snapshot'
        ):
            self.state._validate_state_shape(payload)

    def test_verify_detects_domain_and_mode_state_changes(self) -> None:
        mode_file = pathlib.Path('/etc/hostpanel/webserver-mode')
        snapshot = ('file', 'nginx_apache\n', 0o644, 0, 0, {})
        loaded = (
            mode_file,
            {'example.test': 'hybrid'},
            [('path', mode_file, snapshot)],
        )
        with mock.patch.object(
            self.state, 'load_state', return_value=loaded
        ), mock.patch.object(
            self.state, '_current_domain_modes',
            return_value={'example.test': 'hybrid'},
        ), mock.patch.object(
            self.state.web, '_snapshot_path', return_value=snapshot
        ):
            self.state.verify_state(pathlib.Path('/state'))

        with mock.patch.object(
            self.state, 'load_state', return_value=loaded
        ), mock.patch.object(
            self.state, '_current_domain_modes',
            return_value={'example.test': 'nginx'},
        ):
            with self.assertRaisesRegex(
                self.state.BuildError, 'domain webserver state changed'
            ):
                self.state.verify_state(pathlib.Path('/state'))

        with mock.patch.object(
            self.state, 'load_state', return_value=loaded
        ), mock.patch.object(
            self.state, '_current_domain_modes',
            return_value={'example.test': 'hybrid'},
        ), mock.patch.object(
            self.state.web, '_snapshot_path', return_value=('missing',)
        ):
            with self.assertRaisesRegex(
                self.state.BuildError, 'mode changed during build preparation'
            ):
                self.state.verify_state(pathlib.Path('/state'))

    def test_restore_reverts_domains_before_files_and_mode_state(self) -> None:
        events: list[str] = []
        current = {
            'a.test': 'openlitespeed',
            'b.test': 'openlitespeed',
        }

        def mode_of(domain):
            return current[domain]

        def set_mode(domain, mode, _admin):
            events.append(f'domain:{domain}:{mode}')
            current[domain] = mode
            return {'changed': True}

        server = types.SimpleNamespace(mode_of=mode_of, set_mode=set_mode)
        snapshots = [
            ('path', pathlib.Path('/etc/hostpanel/webserver-mode'), ('missing',))
        ]
        loaded = (
            pathlib.Path('/etc/hostpanel/webserver-mode'),
            {'a.test': 'nginx', 'b.test': 'hybrid'},
            snapshots,
        )
        with mock.patch.object(
            self.state, 'load_state', return_value=loaded
        ), mock.patch.object(
            self.state.web, 'managed_domains', return_value=['a.test', 'b.test']
        ), mock.patch.object(
            self.state.web, 'webserver', server
        ), mock.patch.object(
            self.state.web, '_restore_preparation',
            side_effect=lambda value: events.append('files') or [],
        ):
            self.state.restore_state(pathlib.Path('/state'))

        self.assertEqual(
            events,
            ['domain:b.test:hybrid', 'domain:a.test:nginx', 'files'],
        )

    def test_restore_aggregates_domain_inventory_and_file_errors(self) -> None:
        loaded = (
            pathlib.Path('/etc/hostpanel/webserver-mode'),
            {'missing.test': 'nginx'},
            [('path', pathlib.Path('/etc/hostpanel/webserver-mode'), ('missing',))],
        )
        with mock.patch.object(
            self.state, 'load_state', return_value=loaded
        ), mock.patch.object(
            self.state.web, 'managed_domains', return_value=['extra.test']
        ), mock.patch.object(
            self.state.web, '_restore_preparation',
            return_value=['mode file: restore failed'],
        ):
            with self.assertRaises(self.state.BuildError) as raised:
                self.state.restore_state(pathlib.Path('/state'))
        message = str(raised.exception)
        self.assertIn('managed domains disappeared', message)
        self.assertIn('new managed domains appeared', message)
        self.assertIn('mode file: restore failed', message)

    def test_installed_state_helper_loads_extensionless_web_helper(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            installed = pathlib.Path(directory)
            shutil.copy2(
                TOOLS / 'hostpanel_build_web.py',
                installed / 'hostpanel-build-web',
            )
            for name in (
                'hostpanel_build_web_impl.py',
                'hostpanel_build_web_state.py',
                'hostpanel_build_config.py',
            ):
                shutil.copy2(TOOLS / name, installed / name)

            probe = r'''
import importlib.util
import pathlib
import sys
import types

root = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(root))
store = types.ModuleType('store')
store.connect = lambda: None
modules = types.ModuleType('modules')
modules.webserver = types.SimpleNamespace()
sys.modules['store'] = store
sys.modules['modules'] = modules
path = root / 'hostpanel_build_web_state.py'
spec = importlib.util.spec_from_file_location('installed_web_state', path)
if spec is None or spec.loader is None:
    raise SystemExit('could not create installed state helper spec')
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
expected = root / 'hostpanel-build-web'
if module.web.__file__ != str(expected):
    raise SystemExit(f'unexpected helper path: {module.web.__file__}')
for name in ('capture_state', 'verify_state', 'restore_state', 'load_state'):
    if not callable(getattr(module, name, None)):
        raise SystemExit(f'missing installed state callable: {name}')
'''
            completed = subprocess.run(
                [sys.executable, '-c', probe, str(installed)],
                cwd=installed,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == '__main__':
    unittest.main()
