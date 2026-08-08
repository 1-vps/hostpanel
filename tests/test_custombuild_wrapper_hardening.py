from __future__ import annotations

import importlib.util
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

import hostpanel_build_extras as EXTRAS
import hostpanel_build_powerdns_adapter as POWERDNS
from hostpanel_build_config import BuildError


class FakeModePath:
    def __init__(
        self, value: str = '', *, mode: int = stat.S_IFREG | 0o644,
        uid: int = 0, links: int = 1, missing: bool = False,
    ) -> None:
        self.value = value
        self.metadata = types.SimpleNamespace(
            st_mode=mode, st_uid=uid, st_nlink=links
        )
        self.missing = missing

    def lstat(self):
        if self.missing:
            raise FileNotFoundError
        return self.metadata

    def read_text(self, *, encoding: str):
        if encoding != 'ascii':
            raise AssertionError(encoding)
        return self.value


class WrapperHardeningTests(unittest.TestCase):
    def load_script(self, filename: str):
        name = 'wrapper_' + filename.replace('.', '_')
        spec = importlib.util.spec_from_file_location(name, TOOLS / filename)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    def test_installer_copies_preserved_implementations(self):
        installer = (TOOLS / 'install-hostpanel-build.sh').read_text(
            encoding='utf-8'
        )
        for filename in (
            'hostpanel_build_extras_impl.py',
            'hostpanel_build_powerdns_adapter_impl.py',
            'patch_custombuild_runtime_impl.py',
        ):
            self.assertIn(filename, installer)
            self.assertTrue((TOOLS / filename).is_file())

    def test_powerdns_adapter_reads_only_trusted_mode_file(self):
        original = POWERDNS.operations.DNS_MODE_FILE
        self.addCleanup(
            setattr, POWERDNS.operations, 'DNS_MODE_FILE', original
        )
        POWERDNS.operations.DNS_MODE_FILE = FakeModePath('powerdns')
        self.assertEqual(POWERDNS.applied_dns_mode(), 'powerdns')
        POWERDNS.operations.DNS_MODE_FILE = FakeModePath(missing=True)
        self.assertEqual(POWERDNS.applied_dns_mode(), 'bind')
        unsafe = (
            FakeModePath('powerdns', mode=stat.S_IFLNK | 0o777),
            FakeModePath('powerdns', mode=stat.S_IFREG | 0o666),
            FakeModePath('powerdns', uid=1000),
            FakeModePath('powerdns', links=2),
        )
        for target in unsafe:
            POWERDNS.operations.DNS_MODE_FILE = target
            with self.subTest(metadata=target.metadata), self.assertRaisesRegex(
                BuildError, 'unsafe applied HostPanel DNS mode'
            ):
                POWERDNS.applied_dns_mode()

    def test_powerdns_xattr_copy_removes_inherited_destination_only_acl(self):
        source = pathlib.Path('/source')
        destination = pathlib.Path('/destination')

        def names(path, **kwargs):
            if path == source:
                return ['security.selinux']
            return ['security.selinux', 'system.posix_acl_access']

        with mock.patch.object(
            POWERDNS.os, 'listxattr', side_effect=names
        ), mock.patch.object(
            POWERDNS.os, 'getxattr', return_value=b'label'
        ), mock.patch.object(
            POWERDNS.os, 'setxattr'
        ) as setxattr, mock.patch.object(
            POWERDNS.os, 'removexattr'
        ) as removexattr:
            POWERDNS._copy_xattrs(source, destination)
        removexattr.assert_called_once_with(
            destination, 'system.posix_acl_access', follow_symlinks=False
        )
        setxattr.assert_called_once_with(
            destination, 'security.selinux', b'label', follow_symlinks=False
        )

    def test_varnish_validation_rejects_untrusted_mode_before_commands(self):
        original = EXTRAS.VARNISH_MODE_FILE
        self.addCleanup(setattr, EXTRAS, 'VARNISH_MODE_FILE', original)
        EXTRAS.VARNISH_MODE_FILE = FakeModePath(
            'off', mode=stat.S_IFREG | 0o666
        )
        with mock.patch.object(EXTRAS, 'run_command') as command:
            with self.assertRaisesRegex(BuildError, 'unsafe Varnish runtime mode'):
                EXTRAS.validate_varnish(
                    {'varnish': 'off', 'webserver': 'nginx_apache'},
                    pathlib.Path('/tmp/log'),
                )
        command.assert_not_called()

    def test_custombuild_close_error_unlinks_random_temporary(self):
        module = self.load_script('patch_custombuild_runtime.py')
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            target = root / 'runtime'
            target.write_text('original\n', encoding='utf-8')
            target.chmod(0o640)
            stale = root / '.runtime.custombuild.12345'
            stale.write_text('stale', encoding='ascii')
            real_close = module.os.close

            def close_then_raise(descriptor: int) -> None:
                real_close(descriptor)
                raise OSError('close failed')

            with mock.patch.object(
                module, 'copy_xattrs'
            ), mock.patch.object(
                module.secrets, 'token_hex', return_value='fresh'
            ), mock.patch.object(
                module.os, 'close', side_effect=close_then_raise
            ):
                with self.assertRaisesRegex(OSError, 'close failed'):
                    module.write_atomic(target, 'replacement\n')
            self.assertEqual(target.read_text(encoding='utf-8'), 'original\n')
            self.assertTrue(stale.exists())
            self.assertFalse((root / '.runtime.custombuild.fresh').exists())


if __name__ == '__main__':
    unittest.main()
