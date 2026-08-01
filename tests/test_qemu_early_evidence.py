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
MARKER_NAME = "runner-evidence-state.txt"
MARKER_TEXT = "No VM evidence was produced before the always-run sealing step.\n"


class QemuEarlyEvidenceTests(unittest.TestCase):
    def test_always_step_prepares_marker_before_sanitizing(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        step_start = workflow.index("      - name: Sanitize and seal VM evidence before upload")
        upload_start = workflow.index(
            "      - name: Upload non-sensitive VM evidence",
            step_start,
        )
        block = workflow[step_start:upload_start]

        prepare = block.index("install -d -m 700 artifacts/qemu-vm-acceptance")
        empty_check = block.index(
            "find artifacts/qemu-vm-acceptance -mindepth 1 -print -quit",
            prepare,
        )
        marker = block.index(f"artifacts/qemu-vm-acceptance/{MARKER_NAME}", empty_check)
        sanitize = block.index(
            "python3 tools/sanitize-qemu-evidence.py artifacts/qemu-vm-acceptance",
            marker,
        )
        seal = block.index("python3 tools/seal-qemu-evidence.py", sanitize)

        self.assertEqual(
            block.count("install -d -m 700 artifacts/qemu-vm-acceptance"),
            1,
        )
        self.assertEqual(block.count(MARKER_NAME), 1)
        self.assertIn(MARKER_TEXT.strip(), block)
        self.assertLess(prepare, empty_check)
        self.assertLess(empty_check, marker)
        self.assertLess(marker, sanitize)
        self.assertLess(sanitize, seal)

    def test_empty_private_evidence_directory_gets_a_sealable_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            artifacts = pathlib.Path(temporary_directory) / "artifacts"
            evidence = artifacts / "qemu-vm-acceptance"
            evidence.mkdir(parents=True, mode=0o700)
            evidence.chmod(0o700)
            marker = evidence / MARKER_NAME
            archive = artifacts / "qemu-vm-acceptance.tar"

            marker.write_text(MARKER_TEXT, encoding="utf-8")
            marker.chmod(0o600)

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
            self.assertEqual(marker.read_text(encoding="utf-8"), MARKER_TEXT)
            self.assertEqual(stat.S_IMODE(marker.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(evidence.stat().st_mode), 0o700)

    def test_existing_unsafe_entry_is_not_hidden_by_a_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            evidence = pathlib.Path(temporary_directory) / "qemu-vm-acceptance"
            evidence.mkdir(mode=0o700)
            target = pathlib.Path(temporary_directory) / "outside.txt"
            target.write_text("outside\n", encoding="utf-8")
            (evidence / "unsafe-link").symlink_to(target)

            entries = list(evidence.iterdir())
            if not entries:
                marker = evidence / MARKER_NAME
                marker.write_text(MARKER_TEXT, encoding="utf-8")

            self.assertFalse((evidence / MARKER_NAME).exists())
            sanitized = subprocess.run(
                [sys.executable, str(SANITIZER), str(evidence)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(sanitized.returncode, 0)
            self.assertIn("unsafe evidence file", sanitized.stderr)


if __name__ == "__main__":
    unittest.main()
