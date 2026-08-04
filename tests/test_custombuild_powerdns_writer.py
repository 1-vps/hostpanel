from __future__ import annotations

import pathlib
import stat
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
sys.path.insert(0, str(TOOLS))

import hostpanel_build_powerdns_adapter as ADAPTER
from hostpanel_build_config import BuildError


class PowerDnsWriterTests(unittest.TestCase):
    def test_backend_selection_trusts_directory_and_every_included_conf(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            native = root / 'pdns.conf'
            include = root / 'pdns.d'
            default = include / 'hostpanel-bind.conf'
            ignored = include / '00-comments.conf'
            include.mkdir()
            native.write_text('# native\n', encoding='utf-8')
            ignored.write_text('# no backend here\n', encoding='utf-8')

            def trust(path: pathlib.Path) -> None:
                if path == ignored:
                    raise BuildError(
                        f'unsafe root-managed configuration file: {path}'
                    )

            with mock.patch.object(
                ADAPTER, 'trusted_root_directory_chain'
            ) as trusted_chain, mock.patch.object(
                ADAPTER, 'trusted_root_file', side_effect=trust
            ) as trusted_file:
                with self.assertRaisesRegex(
                    BuildError, 'unsafe root-managed configuration file'
                ):
                    ADAPTER.select_powerdns_backend_config(
                        native, include, default
                    )
            trusted_chain.assert_called_once_with(include)
            self.assertEqual(
                trusted_file.call_args_list,
                [mock.call(native), mock.call(ignored)],
            )

    def test_backend_selection_rejects_unsafe_include_directory_before_glob(self):
        native = pathlib.Path('/etc/powerdns/pdns.conf')
        include = pathlib.Path('/unsafe/pdns.d')
        default = include / 'hostpanel-bind.conf'
        with mock.patch.object(
            ADAPTER, 'trusted_root_file'
        ) as trusted_file, mock.patch.object(
            ADAPTER, 'trusted_root_directory_chain',
            side_effect=BuildError('unsafe root-managed directory'),
        ), mock.patch.object(
            ADAPTER.pathlib.Path, 'glob'
        ) as glob:
            with self.assertRaisesRegex(
                BuildError, 'unsafe root-managed directory'
            ):
                ADAPTER.select_powerdns_backend_config(
                    native, include, default
                )
        trusted_file.assert_called_once_with(native)
        glob.assert_not_called()

    def test_root_writer_preserves_xattrs_and_fsyncs_inode_and_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            target = root / 'mode'
            target.write_text('old\n', encoding='utf-8')
            real_lstat = pathlib.Path.lstat

            def trusted_lstat(path: pathlib.Path):
                if path == root:
                    return types.SimpleNamespace(
                        st_mode=stat.S_IFDIR | 0o755, st_uid=0
                    )
                if path == target:
                    return types.SimpleNamespace(
                        st_mode=stat.S_IFREG | 0o644,
                        st_uid=0, st_gid=0, st_nlink=1,
                    )
                return real_lstat(path)

            copied: list[tuple[pathlib.Path, pathlib.Path]] = []
            with mock.patch.object(
                ADAPTER.operations, 'require_root'
            ), mock.patch.object(
                ADAPTER.pathlib.Path, 'lstat', autospec=True,
                side_effect=trusted_lstat,
            ), mock.patch.object(
                ADAPTER, '_copy_xattrs',
                side_effect=lambda source, temporary: copied.append(
                    (source, temporary)
                ),
            ), mock.patch.object(
                ADAPTER, '_apply_expected_selinux_context'
            ) as expected_context, mock.patch.object(
                ADAPTER.os, 'fsync'
            ) as fsync, mock.patch.object(ADAPTER.os, 'fchown'):
                ADAPTER.write_atomic_root(target, 'new\n', 0o644)
            self.assertEqual(target.read_text(encoding='utf-8'), 'new\n')
            self.assertEqual(len(copied), 1)
            self.assertEqual(copied[0][0], target)
            self.assertNotEqual(copied[0][1], target)
            expected_context.assert_not_called()
            self.assertEqual(fsync.call_count, 3)

    def test_new_root_file_uses_destination_selinux_context(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            target = root / 'new-mode'
            real_lstat = pathlib.Path.lstat

            def trusted_lstat(path: pathlib.Path):
                if path == root:
                    return types.SimpleNamespace(
                        st_mode=stat.S_IFDIR | 0o755, st_uid=0
                    )
                return real_lstat(path)

            with mock.patch.object(
                ADAPTER.operations, 'require_root'
            ), mock.patch.object(
                ADAPTER.pathlib.Path, 'lstat', autospec=True,
                side_effect=trusted_lstat,
            ), mock.patch.object(
                ADAPTER, '_copy_xattrs'
            ) as copy_xattrs, mock.patch.object(
                ADAPTER, '_apply_expected_selinux_context'
            ) as expected_context, mock.patch.object(
                ADAPTER.os, 'fsync'
            ) as fsync, mock.patch.object(ADAPTER.os, 'fchown'):
                ADAPTER.write_atomic_root(target, 'new\n', 0o644)
            self.assertEqual(target.read_text(encoding='utf-8'), 'new\n')
            copy_xattrs.assert_not_called()
            self.assertEqual(expected_context.call_count, 1)
            descriptor, context_path, context_mode = (
                expected_context.call_args.args
            )
            self.assertIsInstance(descriptor, int)
            self.assertEqual(context_path, target)
            self.assertEqual(context_mode, 0o644)
            self.assertEqual(fsync.call_count, 3)

    def test_backend_mode_is_applied_before_xattrs_without_post_swap_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            managed = root / 'named.conf'
            zone_dir = root / 'zones'
            native = root / 'pdns.conf'
            include = root / 'pdns.d'
            target = include / 'hostpanel-bind.conf'
            base_writer = mock.Mock()
            platform = mock.Mock()
            log_path = root / 'build.log'

            def core_configure(_platform, _log_path):
                ADAPTER.operations.write_atomic_root(
                    target, 'launch=bind\n', 0o640
                )

            real_lstat = pathlib.Path.lstat

            def trusted_lstat(path: pathlib.Path):
                if path == include:
                    return types.SimpleNamespace(
                        st_mode=stat.S_IFDIR | 0o755, st_uid=0, st_gid=0
                    )
                if path == target:
                    return types.SimpleNamespace(
                        st_mode=stat.S_IFREG | 0o644,
                        st_uid=0, st_gid=0, st_nlink=1,
                    )
                return real_lstat(path)

            with mock.patch.object(
                ADAPTER.operations, 'dns_layout',
                return_value=(managed, zone_dir, 'bind', 'bind9.service'),
            ), mock.patch.object(
                ADAPTER.operations, 'native_powerdns_config',
                return_value=native,
            ), mock.patch.object(
                ADAPTER.operations, 'powerdns_include_dir',
                return_value=include,
            ), mock.patch.object(
                ADAPTER.operations, 'select_powerdns_backend_config',
                return_value=target,
            ), mock.patch.object(
                ADAPTER.operations, 'write_atomic_root', base_writer
            ), mock.patch.object(
                ADAPTER, '_CORE_CONFIGURE_POWERDNS',
                side_effect=core_configure,
            ), mock.patch.object(
                ADAPTER, 'trusted_root_file'
            ) as trusted_file, mock.patch.object(
                ADAPTER, 'trusted_managed_zone_directory'
            ), mock.patch.object(
                ADAPTER, 'trusted_root_directory_chain'
            ), mock.patch.object(
                ADAPTER.pathlib.Path, 'lstat', autospec=True,
                side_effect=trusted_lstat,
            ), mock.patch.object(
                ADAPTER.os, 'chown'
            ) as chown, mock.patch.object(
                ADAPTER.os, 'chmod'
            ) as chmod:
                ADAPTER._configure_powerdns_readable(platform, log_path)
                self.assertIs(
                    ADAPTER.operations.write_atomic_root, base_writer
                )

            base_writer.assert_called_once_with(
                target, 'launch=bind\n', 0o644
            )
            chown.assert_not_called()
            chmod.assert_not_called()
            self.assertEqual(
                trusted_file.call_args_list,
                [mock.call(managed), mock.call(target)],
            )


if __name__ == '__main__':
    unittest.main()
