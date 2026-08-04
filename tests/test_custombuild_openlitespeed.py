from __future__ import annotations

import importlib.util
import pathlib
import sys
import unittest

_IMPL_PATH = pathlib.Path(__file__).with_name(
    '_custombuild_openlitespeed_tests_impl.py'
)
_SPEC = importlib.util.spec_from_file_location(
    '_custombuild_openlitespeed_tests_impl', _IMPL_PATH
)
if _SPEC is None or _SPEC.loader is None:
    raise ImportError(f'cannot load OpenLiteSpeed test implementation: {_IMPL_PATH}')
_IMPL = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _IMPL
_SPEC.loader.exec_module(_IMPL)


def _apache_mode_reads_runtime_implementation(self) -> None:
    options = dict(_IMPL.config.DEFAULT_OPTIONS)
    options['webserver'] = 'apache'
    self.assertEqual(
        _IMPL.config.web_components(options),
        ['nginx', 'apache', 'php'],
    )
    patcher = (
        _IMPL.TOOLS / 'patch_custombuild_runtime_impl.py'
    ).read_text(encoding='utf-8')
    self.assertIn('# HostPanel Apache-only edge', patcher)
    self.assertIn('proxy_pass http://127.0.0.1:{port}', patcher)
    self.assertIn('APACHE_EDGE_REDIRECT_VHOST', patcher)
    self.assertIn('nginx rejected the Apache edge configuration', patcher)


_IMPL.CustomBuildOpenLiteSpeedTests.test_apache_mode_keeps_the_public_nginx_edge = (
    _apache_mode_reads_runtime_implementation
)

for _name in dir(_IMPL):
    _value = getattr(_IMPL, _name)
    if (
        isinstance(_value, type)
        and issubclass(_value, unittest.TestCase)
        and _value is not unittest.TestCase
    ):
        globals()[_name] = _value


if __name__ == '__main__':
    unittest.main()
