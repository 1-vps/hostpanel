from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
sys.path.insert(0, str(TOOLS))

import hostpanel_build_powerdns_adapter as ADAPTER


class PowerDnsPermissionsAdapterTests(unittest.TestCase):
    def test_readable_backend_paths_rejects_unsafe_objects(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            include = root / 'pdns.d'
            target = include / 'bind.conf'
            include.mkdir()
            target.write_text('launch=bind\n', encoding='utf-8')
            with mock.patch.object(
                ADAPTER.operations, 'native_powerdns_config', return_value=root / 'pdns.conf'
            ), mock.patch.object(
                ADAPTER.operations, 'powerdns_include_dir', return_value=include
            ), mock.patch.object(
                ADAPTER.operations, 'select_powerdns_backend_config', return_value=target
            ), mock.patch.object(ADAPTER, 'trusted_root_directory'), \
                 mock.patch.object(ADAPTER, 'trusted_root_file'):
                self.assertEqual(ADAPTER.readable_backend_paths(), (include, target))

    def test_install_wraps_configuration_before_service_start(self):
        calls: list[str] = []
        original = mock.Mock(side_effect=lambda platform, log: calls.append('configured'))
        include = pathlib.Path('/etc/powerdns/pdns.d')
        target = include / 'bind.conf'
        with mock.patch.object(ADAPTER.operations, 'configure_powerdns', original), \
             mock.patch.object(
                 ADAPTER, 'readable_backend_paths', return_value=(include, target)
             ), mock.patch.object(ADAPTER.os, 'chown') as chown, \
             mock.patch.object(ADAPTER.os, 'chmod') as chmod:
            ADAPTER.install()
            ADAPTER.operations.configure_powerdns(mock.Mock(), pathlib.Path('/tmp/log'))
        self.assertEqual(calls, ['configured'])
        self.assertEqual(
            chmod.call_args_list,
            [mock.call(include, 0o755), mock.call(target, 0o644)],
        )
        self.assertEqual(
            chown.call_args_list,
            [mock.call(include, 0, 0), mock.call(target, 0, 0)],
        )


if __name__ == '__main__':
    unittest.main()
