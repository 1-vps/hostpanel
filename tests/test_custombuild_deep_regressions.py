from __future__ import annotations

import importlib.util
import pathlib
import subprocess
import sys
import unittest
from unittest import mock

_IMPL_PATH = pathlib.Path(__file__).with_name(
    '_custombuild_deep_regressions_impl.py'
)
_SPEC = importlib.util.spec_from_file_location(
    '_custombuild_deep_regressions_impl', _IMPL_PATH
)
if _SPEC is None or _SPEC.loader is None:
    raise ImportError(f'cannot load deep regression implementation: {_IMPL_PATH}')
_IMPL = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _IMPL
_SPEC.loader.exec_module(_IMPL)

POWERDNS = _IMPL.POWERDNS
STATE = _IMPL.STATE


def _dns_handoff_rolls_back_on_non_build_error(self) -> None:
    commands: list[list[str]] = []

    def command(argv, **kwargs):
        commands.append(argv)
        if argv == ['systemctl', 'enable', '--now', 'pdns.service']:
            raise OSError('systemd I/O failed')
        if argv[:2] == ['systemctl', 'is-active']:
            unit = argv[-1]
            active = unit == 'bind9.service'
            return subprocess.CompletedProcess(
                argv, 0 if active else 3,
                'active\n' if active else 'inactive\n', '',
            )
        if argv[:2] == ['systemctl', 'is-enabled']:
            unit = argv[-1]
            enabled = unit == 'bind9.service'
            return subprocess.CompletedProcess(
                argv, 0 if enabled else 1,
                'enabled\n' if enabled else 'disabled\n', '',
            )
        return subprocess.CompletedProcess(argv, 0, '', '')

    with mock.patch.object(POWERDNS.operations, 'dns_layout', return_value=(
        pathlib.Path('/bind'), pathlib.Path('/zones'), 'bind', 'bind9.service'
    )), mock.patch.object(
        POWERDNS.operations, 'service_active', side_effect=[True, False]
    ), mock.patch.object(POWERDNS.operations, 'unmask_service'), \
         mock.patch.object(POWERDNS, 'run_command', side_effect=command):
        with self.assertRaisesRegex(OSError, 'systemd I/O failed'):
            POWERDNS.reconcile_dns_services(
                {'dns': 'powerdns'}, mock.Mock(), pathlib.Path('/tmp/log')
            )
    self.assertIn(['systemctl', 'enable', '--now', 'bind9.service'], commands)


_IMPL.DeepCustomBuildRegressionTests.test_dns_handoff_rolls_back_on_non_build_error = (
    _dns_handoff_rolls_back_on_non_build_error
)


def _with_inactive_optional_service(original):
    def wrapped(self):
        with mock.patch.object(
            STATE, '_service_active', return_value=False
        ), mock.patch.object(
            STATE, '_service_enabled', return_value=False
        ):
            return original(self)
    wrapped.__name__ = original.__name__
    return wrapped


for _method_name in (
    'test_mongodb_masks_service_before_package_scripts',
    'test_varnish_masks_service_checks_ports_and_repairs_proxies',
):
    _original = getattr(_IMPL.DeepCustomBuildRegressionTests, _method_name)
    setattr(
        _IMPL.DeepCustomBuildRegressionTests,
        _method_name,
        _with_inactive_optional_service(_original),
    )


def _without_operations_adapter_install(original):
    def wrapped(self):
        passthrough = lambda function, *args, **kwargs: function(*args, **kwargs)
        with mock.patch.object(
            _IMPL.ENTRY.operations_adapter, 'install'
        ), mock.patch.object(
            _IMPL.ENTRY.web_transaction_adapter,
            'guarded_execute_build',
            side_effect=passthrough,
        ):
            return original(self)
    wrapped.__name__ = original.__name__
    return wrapped


for _method_name in (
    'test_entry_wraps_complete_transaction_idempotently',
    'test_plan_rejects_unsupported_mongodb_target',
):
    _original = getattr(_IMPL.DeepCustomBuildRegressionTests, _method_name)
    setattr(
        _IMPL.DeepCustomBuildRegressionTests,
        _method_name,
        _without_operations_adapter_install(_original),
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
