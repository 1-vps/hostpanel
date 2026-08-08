from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import hostpanel_build_web_transaction_adapter as transaction
from hostpanel_build_config import BuildError


class WebStateSourceBindingTests(unittest.TestCase):
    def test_installed_fallback_is_bound_to_requested_web_helper(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            web_helper = root / 'hostpanel-build-web'
            state_helper = root / 'hostpanel_build_web_state.py'
            web_helper.write_text('# helper\n', encoding='utf-8')
            state_helper.write_text('# state\n', encoding='utf-8')
            with mock.patch.object(transaction, '_trusted_tool') as trusted:
                selected = transaction._state_helper(web_helper)
            self.assertEqual(selected, state_helper)
            self.assertEqual(
                trusted.call_args_list,
                [
                    mock.call(state_helper, executable=False),
                    mock.call(web_helper, executable=True),
                ],
            )

    def test_adjacent_python_shadow_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            web_helper = root / 'hostpanel-build-web'
            state_helper = root / 'hostpanel_build_web_state.py'
            shadow = root / 'hostpanel_build_web.py'
            for path in (web_helper, state_helper, shadow):
                path.write_text('# reviewed-looking file\n', encoding='utf-8')
            with mock.patch.object(transaction, '_trusted_tool') as trusted:
                with self.assertRaisesRegex(
                    BuildError, 'would load a different web runtime'
                ):
                    transaction._state_helper(web_helper)
            trusted.assert_called_once_with(state_helper, executable=False)

    def test_source_layout_binds_to_python_web_helper(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            web_helper = root / 'hostpanel_build_web.py'
            state_helper = root / 'hostpanel_build_web_state.py'
            web_helper.write_text('# source helper\n', encoding='utf-8')
            state_helper.write_text('# state\n', encoding='utf-8')
            with mock.patch.object(transaction, '_trusted_tool') as trusted:
                selected = transaction._state_helper(web_helper)
            self.assertEqual(selected, state_helper)
            self.assertEqual(
                trusted.call_args_list,
                [
                    mock.call(state_helper, executable=False),
                    mock.call(web_helper, executable=False),
                ],
            )


if __name__ == '__main__':
    unittest.main()
