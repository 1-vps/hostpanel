from __future__ import annotations

import importlib.util
import os
import pathlib
import stat
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
HELPER_PATH = ROOT / "tools" / "secure-validation-io.py"


def load_helper():
    spec = importlib.util.spec_from_file_location(
        "secure_validation_io_report_mode_test", HELPER_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load secure validation helper")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ValidationReportDirectoryModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.helper = load_helper()
        cls.uid = os.getuid()

    def test_existing_report_directory_is_tightened_to_0700(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            trusted_root = pathlib.Path(temporary_name)
            trusted_root.chmod(0o700)
            report_directory = trusted_root / "reports"
            report_directory.mkdir(mode=0o755)

            status = self.helper.run_with_report(
                report_directory,
                ["/bin/sh", "-c", "printf 'validated\\n'"],
                owner_uid=self.uid,
                trusted_root=trusted_root,
            )

            self.assertEqual(status, 0)
            self.assertEqual(
                stat.S_IMODE(report_directory.stat().st_mode),
                0o700,
            )
            reports = list(
                report_directory.glob("hostpanel-production-validation-*.log")
            )
            self.assertEqual(len(reports), 1)
            self.assertEqual(stat.S_IMODE(reports[0].stat().st_mode), 0o600)
            self.assertEqual(reports[0].read_text(encoding="utf-8"), "validated\n")


if __name__ == "__main__":
    unittest.main()
