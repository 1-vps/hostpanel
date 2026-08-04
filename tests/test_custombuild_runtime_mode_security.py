from __future__ import annotations

import importlib.util
import pathlib
import stat
import sys
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'


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


class RuntimeModeSecurityTests(unittest.TestCase):
    def load(self, filename: str):
        name = 'mode_security_' + filename.replace('.', '_')
        spec = importlib.util.spec_from_file_location(name, TOOLS / filename)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    def test_powerdns_runtime_rejects_untrusted_mode_files(self):
        module = self.load('patch_powerdns_runtime.py')
        platform = types.SimpleNamespace(service=lambda logical: 'bind9')
        namespace = {'Path': pathlib.Path, 'PLATFORM': platform}
        exec(module.CORE_NEW, namespace)

        namespace['DNS_MODE_FILE'] = FakeModePath('powerdns')
        self.assertEqual(namespace['service_name']('dns'), 'pdns')
        namespace['DNS_MODE_FILE'] = FakeModePath(missing=True)
        self.assertEqual(namespace['service_name']('dns'), 'bind9')

        unsafe = (
            FakeModePath('powerdns', mode=stat.S_IFLNK | 0o777),
            FakeModePath('powerdns', mode=stat.S_IFREG | 0o666),
            FakeModePath('powerdns', links=2),
            FakeModePath('powerdns', uid=1000),
        )
        for target in unsafe:
            with self.subTest(metadata=target.metadata):
                namespace['DNS_MODE_FILE'] = target
                with self.assertRaisesRegex(RuntimeError, 'unsafe'):
                    namespace['service_name']('dns')

        namespace['DNS_MODE_FILE'] = FakeModePath('invalid')
        with self.assertRaisesRegex(RuntimeError, 'invalid'):
            namespace['service_name']('dns')
        self.assertIn("stat -Lc '%u:%h:%a'", module.ROOT_MODE)
        self.assertIn('[[ -f "$DNS_MODE_FILE" && ! -L', module.ROOT_MODE)

    def test_varnish_runtime_rejects_untrusted_mode_files(self):
        module = self.load('patch_varnish_runtime.py')
        namespace = {'Path': pathlib.Path}
        exec(module.CONSTANTS_NEW, namespace)

        namespace['VARNISH_MODE_FILE'] = FakeModePath('on')
        self.assertTrue(namespace['varnish_enabled']())
        namespace['VARNISH_MODE_FILE'] = FakeModePath(missing=True)
        self.assertFalse(namespace['varnish_enabled']())

        for target in (
            FakeModePath('on', mode=stat.S_IFLNK | 0o777),
            FakeModePath('on', mode=stat.S_IFREG | 0o664),
            FakeModePath('on', links=2),
            FakeModePath('on', uid=1000),
        ):
            namespace['VARNISH_MODE_FILE'] = target
            with self.assertRaisesRegex(RuntimeError, 'unsafe'):
                namespace['varnish_enabled']()

    def test_doctor_uses_trusted_reader_for_all_runtime_modes(self):
        module = self.load('patch_extras_doctor.py')
        source = (
            'def inspect(roles, expected, service, Path):\n'
            + module.EXTRAS_BLOCK
            + '    return expected\n'
        )
        namespace: dict[str, object] = {}
        exec(source, namespace)

        paths = {
            '/etc/hostpanel/dns-mode': FakeModePath('powerdns'),
            '/etc/hostpanel/mongodb-mode': FakeModePath('8.0'),
            '/etc/hostpanel/varnish-mode': FakeModePath('on'),
        }
        result = namespace['inspect'](
            {'dns', 'database', 'web'}, {}, lambda logical: 'bind9',
            lambda value: paths[value],
        )
        self.assertEqual(
            result, {'pdns': True, 'mongod': True, 'varnish': True}
        )

        paths['/etc/hostpanel/mongodb-mode'] = FakeModePath(
            '8.0', mode=stat.S_IFREG | 0o666
        )
        with self.assertRaisesRegex(RuntimeError, 'unsafe'):
            namespace['inspect'](
                {'database'}, {}, lambda logical: 'bind9',
                lambda value: paths[value],
            )


if __name__ == '__main__':
    unittest.main()
