from __future__ import annotations

import pathlib

import test_custombuild_openlitespeed as discovered

_IMPL = discovered._IMPL


def _domain_switch_has_reverse_rollback(self) -> None:
    source = (
        _IMPL.TOOLS / 'hostpanel_build_web_impl.py'
    ).read_text(encoding='utf-8')
    self.assertIn('for domain in reversed(changed):', source)
    self.assertIn(
        'webserver.set_mode(domain, previous[domain], admin)', source
    )
    self.assertIn('webserver switch failed and was rolled back', source)


def _check_path_does_not_write_lsphp_state(self) -> None:
    source = (
        _IMPL.TOOLS / 'hostpanel_build_web_impl.py'
    ).read_text(encoding='utf-8')
    check_body = source.split('def check_openlitespeed', 1)[1].split(
        'def rollback_domains', 1
    )[0]
    self.assertIn('validate_lsphp_runtimes(options)', check_body)
    self.assertNotIn('ensure_lsphp_runtimes(options)', check_body)
    self.assertNotIn('write_new_root_file', check_body)


_IMPL.CustomBuildOpenLiteSpeedTests.test_domain_switch_has_reverse_rollback = (
    _domain_switch_has_reverse_rollback
)
_IMPL.CustomBuildOpenLiteSpeedTests.test_check_path_does_not_write_lsphp_state = (
    _check_path_does_not_write_lsphp_state
)
