#!/usr/bin/env python3
from __future__ import annotations

import pathlib
import stat
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "qemu-vm-acceptance.yml"
SANITIZER = ROOT / "tools" / "sanitize-qemu-evidence.py"
SEALER = ROOT / "tools" / "seal-qemu-evidence.py"


class QemuEarlyEvidenceTests(unittest.TestCase):
    def test_always_step_prepares_private_directory_before_sanitizing(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        step_start = workflow.index("      - name: Sanitize and seal VM evidence before upload")
        upload_start = workflow.index(
            "      - name: Upload non-sensitive VM evidence",
            step_start,
        )
        block = workflow[step_start:upload_start]

        prepare = block.index("install -d -m 700 artifacts/qemu-vm-acceptance")
        sanitize = block.index(
            "python3 tools/sanitize-qemu-evidence.py artifacts/qemu-vm-acceptance"
        )
        seal = block.index("python3 tools/seal-qemu-evidence.py", sanitize)

        self.assertEqual(
            block.count("install -d -m 700 artifacts/qemu-vm-acceptance"),
            1,
        )
        self.assertLess(prepare, sanitize)
        self.assertLess(sanitize, seal)

    def test_empty_private_evidence_directory_can_be_sealed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            artifacts = pathlib.Path(temporary_directory) / "artifacts"
            evidence = artifacts / "qemu-vm-acceptance"
            evidence.mkdir(parents=True, mode=0o700)
            evidence.chmod(0o700)
            archive = artifacts / "qemu-vm-acceptance.tar"

            sanitized = subprocess.run(
                [sys.executable, str(SANITIZER), str(evidence)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(sanitized.returncode, 0, sanitized.stderr)

            sealed = subprocess.run(
                [sys.executable, str(SEALER), str(evidence), str(archive)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(sealed.returncode, 0, sealed.stderr)
            self.assertTrue(archive.is_file())
            self.assertEqual(stat.S_IMODE(evidence.stat().st_mode), 0o700)


if __name__ == "__main__":
    unittest.main()
