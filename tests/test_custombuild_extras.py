from __future__ import annotations

import importlib.util
import pathlib
import sys
import unittest

_IMPL_PATH = pathlib.Path(__file__).with_name(
    '_custombuild_extras_tests_impl.py'
)
_SPEC = importlib.util.spec_from_file_location(
    '_custombuild_extras_tests_impl', _IMPL_PATH
)
if _SPEC is None or _SPEC.loader is None:
    raise ImportError(f'cannot load extras test implementation: {_IMPL_PATH}')
_IMPL = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _IMPL
_SPEC.loader.exec_module(_IMPL)


def _mongodb_repo_contract_uses_implementation(self) -> None:
    source = (
        _IMPL.TOOLS / 'hostpanel_build_extras_impl.py'
    ).read_text(encoding='utf-8')
    self.assertIn(
        "MONGODB_KEY_FINGERPRINT = "
        "'4B0752C1BCA238C0B4EE14DC41DE058A4E7DCA05'",
        source,
    )
    self.assertIn("'curl', '-q'", source)
    self.assertIn("'--proto', '=https'", source)
    self.assertIn('https://repo.mongodb.org/apt/ubuntu', source)
    self.assertIn('https://repo.mongodb.org/yum/redhat/', source)


_IMPL.CustomBuildExtrasTests.test_mongodb_repo_contract_uses_https_and_pinned_key = (
    _mongodb_repo_contract_uses_implementation
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
