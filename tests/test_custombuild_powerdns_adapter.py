from __future__ import annotations

import pathlib
import stat
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
sys.path.insert(0, str(TOOLS))

import hostpanel_build_config as CONFIG
import hostpanel_build_powerdns_adapter as ADAPTER


class PowerDnsPermissionsAdapterTests(unittest.TestCase):
    def test_native_config_requires_exactly_one_trusted_candidate(self):
        first = pathlib.Path('/etc/powerdns/pdns.conf')
        second = pathlib.Path('/etc/pdns/pdns.conf')
        with mock.patch.object(
            ADAPTER.operations, 'PDNS_CONFIG_CANDIDATES', (first, second)
        ), mock.patch.object(ADAPTER.os.path, 'lexists', return_value=False):
            with self.assertRaisesRegex(CONFIG.BuildError, 'exactly one'):
                ADAPTER.native_powerdns_config()
        with mock.patch.object(
            ADAPTER.operations, 'PDNS_CONFIG_CANDIDATES', (first, second)
        ), mock.patch.object(ADAPTER.os.path, 'lexists', return_value=True):
            with self.assertRaisesRegex(CONFIG.BuildError, 'exactly one'):
                ADAPTER.native_powerdns_config()
        with mock.patch.object(
            ADAPTER.operations, 'PDNS_CONFIG_CANDIDATES', (first, second)
        ), mock.patch.object(
            ADAPTER.os.path, 'lexists', side_effect=lambda path: path == first
        ), mock.patch.object(ADAPTER, 'trusted_root_file') as trusted:
            self.assertEqual(ADAPTER.native_powerdns_config(), first)
        trusted.assert_called_once_with(first)

    def test_managed_zone_directory_accepts_service_owner_and_private_mode(self):
        metadata = mock.Mock(
            st_mode=stat.S_IFDIR | 0o750, st_uid=123, st_gid=456
        )
        with mock.patch.object(ADAPTER, 'trusted_root_directory_chain'), \
             mock.patch.object(pathlib.Path, 'lstat', return_value=metadata), \
             mock.patch.object(ADAPTER.pwd, 'getpwnam', return_value=mock.Mock(pw_uid=123)), \
             mock.patch.object(ADAPTER.grp, 'getgrnam', return_value=mock.Mock(gr_gid=456)):
            ADAPTER.trusted_managed_zone_directory(pathlib.Path('/zones'), 'bind')
        metadata.st_mode = stat.S_IFDIR | 0o770
        with mock.patch.object(ADAPTER, 'trusted_root_directory_chain'), \
             mock.patch.object(pathlib.Path, 'lstat', return_value=metadata), \
             mock.patch.object(ADAPTER.pwd, 'getpwnam', return_value=mock.Mock(pw_uid=123)), \
             mock.patch.object(ADAPTER.grp, 'getgrnam', return_value=mock.Mock(gr_gid=456)):
            with self.assertRaisesRegex(CONFIG.BuildError, 'unsafe HostPanel managed zone'):
                ADAPTER.trusted_managed_zone_directory(pathlib.Path('/zones'), 'bind')

    def test_readable_backend_paths_validates_directory_chain_and_file(self):
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
            ), mock.patch.object(ADAPTER, 'trusted_root_directory_chain') as chain, \
                 mock.patch.object(ADAPTER, 'trusted_root_file') as trusted_file:
                self.assertEqual(ADAPTER.readable_backend_paths(), (include, target))
        chain.assert_called_once_with(include)
        trusted_file.assert_called_once_with(target)

    def test_install_prevalidates_sources_before_configuration(self):
        calls: list[str] = []
        original = mock.Mock(side_effect=lambda platform, log: calls.append('configured'))
        include = pathlib.Path('/etc/powerdns/pdns.d')
        target = include / 'bind.conf'
        managed = pathlib.Path('/etc/bind/named.conf.local')
        zones = pathlib.Path('/etc/bind/zones')
        with mock.patch.object(ADAPTER.operations, 'configure_powerdns', original), \
             mock.patch.object(
                 ADAPTER.operations, 'dns_layout',
                 return_value=(managed, zones, 'bind', 'bind9.service'),
             ), mock.patch.object(ADAPTER, 'trusted_root_file') as trusted_file, \
             mock.patch.object(ADAPTER, 'trusted_managed_zone_directory') as trusted_zone, \
             mock.patch.object(
                 ADAPTER, 'readable_backend_paths', return_value=(include, target)
             ), mock.patch.object(ADAPTER.os, 'chown') as chown, \
             mock.patch.object(ADAPTER.os, 'chmod') as chmod:
            ADAPTER.install()
            ADAPTER.operations.configure_powerdns(mock.Mock(), pathlib.Path('/tmp/log'))
        self.assertEqual(calls, ['configured'])
        trusted_file.assert_any_call(managed)
        trusted_zone.assert_called_once_with(zones, 'bind')
        self.assertEqual(
            chmod.call_args_list,
            [mock.call(include, 0o755), mock.call(target, 0o644)],
        )
        self.assertEqual(
            chown.call_args_list,
            [mock.call(include, 0, 0), mock.call(target, 0, 0)],
        )

    def test_install_replaces_all_security_sensitive_operations(self):
        with mock.patch.object(ADAPTER.operations, 'configure_powerdns'):
            ADAPTER.install()
        self.assertIs(ADAPTER.operations.reconcile_dns_services, ADAPTER.reconcile_dns_services)
        self.assertIs(ADAPTER.operations.native_powerdns_config, ADAPTER.native_powerdns_config)
        self.assertIs(ADAPTER.operations.mask_service, ADAPTER.mask_service)


if __name__ == '__main__':
    unittest.main()
