#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import pathlib
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SEALER = ROOT / "tools" / "seal-qemu-evidence.py"
WORKFLOW = ROOT / ".github" / "workflows" / "qemu-vm-acceptance.yml"


class QemuEvidenceSealerTests(unittest.TestCase):
    def run_sealer(
        self,
        evidence: pathlib.Path,
        archive: pathlib.Path,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SEALER), str(evidence), str(archive)],
            check=False,
            text=True,
            capture_output=True,
        )

    def test_sealed_snapshot_is_sanitized_and_immune_to_later_source_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = pathlib.Path(temporary_directory)
            evidence = root / "evidence"
            evidence.mkdir()
            source = evidence / "runner.log"
            source.write_bytes(b"phase=install password=initial-secret\n")
            archive = root / "evidence.tar"

            result = self.run_sealer(evidence, archive)

            self.assertEqual(result.returncode, 0, result.stderr)
            before = hashlib.sha256(archive.read_bytes()).digest()
            source.write_bytes(b"password=later-secret\n")
            (evidence / "late.txt").write_bytes(b"password=new-secret\n")
            self.assertEqual(hashlib.sha256(archive.read_bytes()).digest(), before)

            with tarfile.open(archive, "r:") as sealed:
                self.assertEqual(
                    sealed.getnames(),
                    ["qemu-vm-acceptance/runner.log"],
                )
                member = sealed.getmembers()[0]
                handle = sealed.extractfile(member)
                self.assertIsNotNone(handle)
                assert handle is not None
                content = handle.read()
            self.assertIn(b"password=[REDACTED]", content)
            self.assertNotIn(b"initial-secret", content)
            self.assertNotIn(b"later-secret", archive.read_bytes())
            self.assertNotIn(b"new-secret", archive.read_bytes())
            self.assertEqual(stat.S_IMODE(archive.stat().st_mode), 0o600)
            self.assertEqual(member.mode, 0o600)
            self.assertEqual((member.uid, member.gid, member.mtime), (0, 0, 0))
            self.assertTrue(member.isfile())

    def test_symlinked_root_is_rejected_before_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = pathlib.Path(temporary_directory)
            real_evidence = root / "real-evidence"
            real_evidence.mkdir()
            (real_evidence / "safe.txt").write_text("safe\n", encoding="utf-8")
            evidence = root / "evidence"
            evidence.symlink_to(real_evidence, target_is_directory=True)
            archive = root / "evidence.tar"

            result = self.run_sealer(evidence, archive)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unsafe evidence root", result.stderr)
            self.assertFalse(archive.exists())

    def test_symlinked_output_parent_is_rejected_before_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = pathlib.Path(temporary_directory)
            evidence = root / "evidence"
            evidence.mkdir()
            (evidence / "safe.txt").write_text("safe\n", encoding="utf-8")
            real_output = root / "real-output"
            real_output.mkdir()
            output_link = root / "output"
            output_link.symlink_to(real_output, target_is_directory=True)
            archive = output_link / "evidence.tar"

            result = self.run_sealer(evidence, archive)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unsafe sealed evidence parent", result.stderr)
            self.assertFalse((real_output / "evidence.tar").exists())

    def test_unsafe_source_fails_without_publishing_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = pathlib.Path(temporary_directory)
            evidence = root / "evidence"
            evidence.mkdir()
            outside = root / "outside.txt"
            outside.write_text("password=outside-secret", encoding="utf-8")
            (evidence / "linked.txt").symlink_to(outside)
            archive = root / "evidence.tar"

            result = self.run_sealer(evidence, archive)

            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(archive.exists())
            self.assertEqual(outside.read_text(encoding="utf-8"), "password=outside-secret")

    def test_existing_archive_is_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = pathlib.Path(temporary_directory)
            evidence = root / "evidence"
            evidence.mkdir()
            (evidence / "safe.txt").write_text("safe\n", encoding="utf-8")
            archive = root / "evidence.tar"
            archive.write_bytes(b"sentinel")

            result = self.run_sealer(evidence, archive)

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(archive.read_bytes(), b"sentinel")

    def test_archive_is_created_directly_through_stable_descriptors(self) -> None:
        sealer = SEALER.read_text(encoding="utf-8")
        self.assertIn("os.O_EXCL", sealer)
        self.assertIn("dir_fd=parent_descriptor", sealer)
        self.assertIn("os.fchmod(archive_descriptor, 0o600)", sealer)
        self.assertIn("os.fsync(archive_descriptor)", sealer)
        self.assertIn(
            "_same_file(final_descriptor_metadata, final_path_metadata)",
            sealer,
        )
        self.assertNotIn("tempfile", sealer)
        self.assertNotIn("os.link(", sealer)

    def test_workflow_uploads_only_the_sealed_snapshot(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        validation = workflow.index("test_qemu_evidence_sealer.py")
        sanitizer = workflow.index(
            "python3 tools/sanitize-qemu-evidence.py artifacts/qemu-vm-acceptance"
        )
        sealer = workflow.index(
            "python3 tools/seal-qemu-evidence.py \\\n"
            "            artifacts/qemu-vm-acceptance \\\n"
            "            artifacts/qemu-vm-acceptance.tar",
            sanitizer,
        )
        upload = workflow.index("name: Upload non-sensitive VM evidence", sealer)
        archive_path = workflow.index(
            "          path: artifacts/qemu-vm-acceptance.tar",
            upload,
        )
        self.assertLess(validation, sanitizer)
        self.assertLess(sanitizer, sealer)
        self.assertLess(sealer, upload)
        self.assertLess(upload, archive_path)
        self.assertNotIn(
            "          path: artifacts/qemu-vm-acceptance\n",
            workflow,
        )
        self.assertIn("      - tools/seal-qemu-evidence.py", workflow)
        self.assertIn("      - tests/test_qemu_evidence_sealer.py", workflow)


if __name__ == "__main__":
    unittest.main()
