from __future__ import annotations

import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import hostpanel_build_extras_state as state
from hostpanel_build_config import BuildError, Platform


class MongoDbRepositoryRollbackTests(unittest.TestCase):
    def invoke(self) -> None:
        state.apply_mongodb(
            {'mongodb': '8.0'},
            Platform('debian', 'ubuntu', '24.04'),
            pathlib.Path('/log'),
            pathlib.Path('/backup'),
        )

    def test_repository_files_are_captured_before_transaction_and_restored(self) -> None:
        events: list[tuple[str, pathlib.Path | None]] = []
        expected_paths = state._mongodb_repository_paths()

        def capture(path: pathlib.Path):
            events.append(('capture', path))
            return ('snapshot', path)

        def transaction(*_args, **_kwargs):
            events.append(('transaction', None))
            raise BuildError('repository refresh failed')

        def restore(path: pathlib.Path, captured):
            self.assertEqual(captured, ('snapshot', path))
            events.append(('restore', path))

        with mock.patch.object(state._IMPL.base, 'require_root'), \
             mock.patch.object(state, '_capture', side_effect=capture), \
             mock.patch.object(
                 state, '_apply_with_post_validation', side_effect=transaction
             ), mock.patch.object(state, '_restore', side_effect=restore):
            with self.assertRaisesRegex(
                BuildError, 'repository refresh failed'
            ):
                self.invoke()

        self.assertEqual(
            events,
            [('capture', path) for path in expected_paths]
            + [('transaction', None)]
            + [('restore', path) for path in expected_paths],
        )

    def test_repository_rollback_errors_are_combined_with_original_failure(self) -> None:
        first = state._mongodb_repository_paths()[0]

        def restore(path: pathlib.Path, _captured) -> None:
            if path == first:
                raise OSError('restore write failed')

        with mock.patch.object(state._IMPL.base, 'require_root'), \
             mock.patch.object(state, '_capture', return_value=None), \
             mock.patch.object(
                 state,
                 '_apply_with_post_validation',
                 side_effect=BuildError('candidate unavailable'),
             ), mock.patch.object(state, '_restore', side_effect=restore):
            with self.assertRaises(BuildError) as raised:
                self.invoke()

        message = str(raised.exception)
        self.assertIn('candidate unavailable', message)
        self.assertIn('repository rollback also failed', message)
        self.assertIn(str(first), message)
        self.assertIn('restore write failed', message)

    def test_mongodb_off_does_not_touch_repository_state(self) -> None:
        with mock.patch.object(state, '_capture') as capture, \
             mock.patch.object(
                 state, '_apply_with_post_validation'
             ) as transaction:
            state.apply_mongodb(
                {'mongodb': 'off'},
                Platform('debian', 'ubuntu', '24.04'),
                pathlib.Path('/log'),
                pathlib.Path('/backup'),
            )
        capture.assert_not_called()
        transaction.assert_called_once()


if __name__ == '__main__':
    unittest.main()
