#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import pathlib
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "qemu-vm-acceptance.yml"
PREPARER = ROOT / "tools" / "prepare-qemu-evidence.py"
SANITIZER = ROOT / "tools" / "sanitize-qemu-evidence.py"
SEALER = ROOT / "tools" / "seal-qemu-evidence.py"
MARKER_NAME = "runner-evidence-state.txt"
MARKER_TEXT = "No VM evidence was produced before the always-run sealing step.\n"
INVALID_FLAG_VALUES = (None, 0, True, -1)


def load_preparer_module():
    spec = importlib.util.spec_from_file_location("hostpanel_qemu_evidence_preparer", PREPARER)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load QEMU evidence preparer")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class QemuEarlyEvidenceTests(unittest.TestCase):
    def run_preparer(self, directory: pathlib.Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(PREPARER)],
            cwd=directory,
            check=False,
            capture_output=True,
            text=True,
        )

    def test_workflow_prepares_evidence_before_sanitizing(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        step_start = workflow.index("      - name: Sanitize and seal VM evidence before upload")
        upload_start = workflow.index(
            "      - name: Upload non-sensitive VM evidence",
            step_start,
        )
        block = workflow[step_start:upload_start]

        prepare = block.index("python3 tools/prepare-qemu-evidence.py")
        sanitize = block.index(
            "python3 tools/sanitize-qemu-evidence.py artifacts/qemu-vm-acceptance",
            prepare,
        )
        seal = block.index("python3 tools/seal-qemu-evidence.py", sanitize)

        self.assertEqual(block.count("python3 tools/prepare-qemu-evidence.py"), 1)
        self.assertLess(prepare, sanitize)
        self.assertLess(sanitize, seal)
        self.assertIn("      - tools/prepare-qemu-evidence.py", workflow)
        self.assertGreaterEqual(workflow.count("test_qemu_early_evidence.py"), 2)

    def test_unavailable_directory_open_flag_fails_closed(self) -> None:
        module = load_preparer_module()
        for unavailable_value in INVALID_FLAG_VALUES:
            with self.subTest(value=unavailable_value):
                with mock.patch.object(module.os, "O_DIRECTORY", unavailable_value):
                    with self.assertRaisesRegex(
                        RuntimeError,
                        "requires O_DIRECTORY; unsupported platform",
                    ):
                        module._open_directory(".")

    def test_unavailable_no_follow_flag_fails_closed_for_directories(self) -> None:
        module = load_preparer_module()
        for unavailable_value in INVALID_FLAG_VALUES:
            with self.subTest(value=unavailable_value):
                with mock.patch.object(module.os, "O_NOFOLLOW", unavailable_value):
                    with self.assertRaisesRegex(
                        RuntimeError,
                        "requires O_NOFOLLOW; unsupported platform",
                    ):
                        module._open_directory(".")

    def test_unavailable_no_follow_flag_cannot_create_a_marker(self) -> None:
        module = load_preparer_module()
        for unavailable_value in INVALID_FLAG_VALUES:
            with self.subTest(value=unavailable_value):
                with tempfile.TemporaryDirectory() as temporary_directory:
                    evidence = pathlib.Path(temporary_directory)
                    evidence_fd = module._open_directory(str(evidence))
                    try:
                        with mock.patch.object(module.os, "O_NOFOLLOW", unavailable_value):
                            with self.assertRaisesRegex(
                                RuntimeError,
                                "requires O_NOFOLLOW; unsupported platform",
                            ):
                                module._create_marker(evidence_fd)
                    finally:
                        module.os.close(evidence_fd)
                    self.assertFalse((evidence / MARKER_NAME).exists())

    def test_empty_private_evidence_directory_gets_a_sealable_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = pathlib.Path(temporary_directory)
            result = self.run_preparer(root)

            self.assertEqual(result.returncode, 0, result.stderr)
            evidence = root / "artifacts" / "qemu-vm-acceptance"
            marker = evidence / MARKER_NAME
            archive = root / "artifacts" / "qemu-vm-acceptance.tar"
            self.assertEqual(marker.read_text(encoding="utf-8"), MARKER_TEXT)
            self.assertEqual(stat.S_IMODE(marker.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(evidence.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(evidence.parent.stat().st_mode), 0o700)

            repeated = self.run_preparer(root)
            self.assertEqual(repeated.returncode, 0, repeated.stderr)
            self.assertEqual(marker.read_text(encoding="utf-8"), MARKER_TEXT)
            self.assertEqual(stat.S_IMODE(marker.stat().st_mode), 0o600)

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

    def test_corrupted_existing_marker_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = pathlib.Path(temporary_directory)
            evidence = root / "artifacts" / "qemu-vm-acceptance"
            evidence.mkdir(parents=True, mode=0o700)
            marker = evidence / MARKER_NAME
            marker.write_text("partial\n", encoding="utf-8")
            marker.chmod(0o600)

            result = self.run_preparer(root)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unexpected size", result.stderr)
            self.assertEqual(marker.read_text(encoding="utf-8"), "partial\n")

    def test_existing_evidence_is_not_replaced_by_a_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = pathlib.Path(temporary_directory)
            evidence = root / "artifacts" / "qemu-vm-acceptance"
            evidence.mkdir(parents=True, mode=0o700)
            existing = evidence / "stage.txt"
            existing.write_text("existing evidence\n", encoding="utf-8")

            result = self.run_preparer(root)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(existing.read_text(encoding="utf-8"), "existing evidence\n")
            self.assertFalse((evidence / MARKER_NAME).exists())

    def test_existing_unsafe_entry_is_not_hidden_by_a_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = pathlib.Path(temporary_directory)
            evidence = root / "artifacts" / "qemu-vm-acceptance"
            evidence.mkdir(parents=True, mode=0o700)
            target = root / "outside.txt"
            target.write_text("outside\n", encoding="utf-8")
            (evidence / "unsafe-link").symlink_to(target)

            prepared = self.run_preparer(root)

            self.assertEqual(prepared.returncode, 0, prepared.stderr)
            self.assertFalse((evidence / MARKER_NAME).exists())
            sanitized = subprocess.run(
                [sys.executable, str(SANITIZER), str(evidence)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(sanitized.returncode, 0)
            self.assertIn("unsafe evidence file", sanitized.stderr)

    def test_symlinked_evidence_directory_is_rejected_without_writing_outside(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = pathlib.Path(temporary_directory)
            artifacts = root / "artifacts"
            artifacts.mkdir(mode=0o700)
            outside = root / "outside"
            outside.mkdir(mode=0o700)
            (artifacts / "qemu-vm-acceptance").symlink_to(outside, target_is_directory=True)

            result = self.run_preparer(root)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("filesystem error", result.stderr)
            self.assertFalse((outside / MARKER_NAME).exists())

    def test_symlinked_artifact_parent_is_rejected_without_writing_outside(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = pathlib.Path(temporary_directory)
            outside = root / "outside"
            outside.mkdir(mode=0o700)
            (root / "artifacts").symlink_to(outside, target_is_directory=True)

            result = self.run_preparer(root)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("filesystem error", result.stderr)
            self.assertFalse((outside / "qemu-vm-acceptance").exists())


if __name__ == "__main__":
    unittest.main()
