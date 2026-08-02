from __future__ import annotations

import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "tools" / "validate-production-vm.sh"


class SecureValidatorReportLocationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = VALIDATOR.read_text(encoding="utf-8")

    def test_default_report_directory_is_under_private_state_tree(self) -> None:
        self.assertIn(
            'REPORT_DIR="${HP_VALIDATION_REPORT_DIR:-$STATE_DIR/reports}"',
            self.source,
        )
        self.assertNotIn('HP_VALIDATION_REPORT_DIR:-/var/log', self.source)
        state = self.source.index(
            '"$PYTHON3" -I "$VALIDATION_IO" ensure-directory "$STATE_DIR" 700'
        )
        reports = self.source.index(
            '"$PYTHON3" -I "$VALIDATION_IO" ensure-directory "$REPORT_DIR" 700'
        )
        wrapper = self.source.index(
            '"$PYTHON3" -I "$VALIDATION_IO" run-with-report "$REPORT_DIR"'
        )
        self.assertLess(state, reports)
        self.assertLess(reports, wrapper)

    def test_report_directory_is_root_private_and_umask_independent(self) -> None:
        self.assertIn(
            '"$PYTHON3" -I "$VALIDATION_IO" ensure-directory "$REPORT_DIR" 700',
            self.source,
        )
        self.assertNotIn(
            '"$PYTHON3" -I "$VALIDATION_IO" ensure-directory "$REPORT_DIR" 755',
            self.source,
        )

    def test_report_override_still_passes_through_descriptor_validation(self) -> None:
        self.assertIn("HP_VALIDATION_REPORT_DIR", self.source)
        self.assertEqual(self.source.count("run-with-report"), 1)
        self.assertNotIn(': >"$REPORT"', self.source)
        self.assertNotIn('tee -a "$REPORT"', self.source)


if __name__ == "__main__":
    unittest.main()
