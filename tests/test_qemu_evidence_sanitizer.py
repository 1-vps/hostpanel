#!/usr/bin/env python3
from __future__ import annotations

import pathlib
import stat
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SANITIZER = ROOT / "tools" / "sanitize-qemu-evidence.py"
WORKFLOW = ROOT / ".github" / "workflows" / "qemu-vm-acceptance.yml"


class QemuEvidenceSanitizerTests(unittest.TestCase):
    def run_sanitizer(self, directory: pathlib.Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SANITIZER), str(directory)],
            check=False,
            text=True,
            capture_output=True,
        )

    def test_redacts_secret_shaped_evidence_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            evidence = pathlib.Path(temporary_directory)
            output = evidence / "failure.txt"
            output.write_bytes(
                b"database=postgresql://hostpanel:database-secret@db.internal/panel\n"
                b"Authorization: Bearer bearer-secret-value\n"
                b"https://x-access-token:ghp_123456789012345678901234567890@github.com/repo\n"
                b"https://example.invalid/?access_token=query-secret&mode=test\n"
                b"github_pat_123456789012345678901234567890\n"
                b"-----BEGIN OPENSSH PRIVATE KEY-----\nprivate-material\n"
                b"-----END OPENSSH PRIVATE KEY-----\n"
            )

            result = self.run_sanitizer(evidence)

            self.assertEqual(result.returncode, 0, result.stderr)
            sanitized = output.read_bytes()
            for secret in (
                b"database-secret",
                b"bearer-secret-value",
                b"ghp_123456789012345678901234567890",
                b"query-secret",
                b"github_pat_123456789012345678901234567890",
                b"private-material",
            ):
                self.assertNotIn(secret, sanitized)
            self.assertIn(b"postgresql://hostpanel:[REDACTED]@db.internal", sanitized)
            self.assertIn(b"Authorization: Bearer [REDACTED]", sanitized)
            self.assertIn(b"x-access-token:[REDACTED]@github.com", sanitized)
            self.assertIn(b"access_token=[REDACTED]&mode=test", sanitized)
            self.assertIn(b"[REDACTED_GITHUB_TOKEN]", sanitized)
            self.assertIn(b"[REDACTED_PRIVATE_KEY]", sanitized)
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)

    def test_clean_evidence_is_not_rewritten(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            evidence = pathlib.Path(temporary_directory)
            output = evidence / "validator.txt"
            output.write_text("validator passed\n", encoding="utf-8")
            before = output.stat().st_ino

            result = self.run_sanitizer(evidence)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output.read_text(encoding="utf-8"), "validator passed\n")
            self.assertEqual(output.stat().st_ino, before)

    def test_symlinked_evidence_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = pathlib.Path(temporary_directory)
            evidence = root / "evidence"
            evidence.mkdir()
            outside = root / "outside.txt"
            outside.write_text("outside-secret", encoding="utf-8")
            (evidence / "linked.txt").symlink_to(outside)

            result = self.run_sanitizer(evidence)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unsafe evidence file", result.stderr)
            self.assertEqual(outside.read_text(encoding="utf-8"), "outside-secret")

    def test_secret_shaped_filename_fails_without_echoing_secret(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            evidence = pathlib.Path(temporary_directory)
            secret_name = "github_pat_123456789012345678901234567890.txt"
            (evidence / secret_name).write_text("non-secret content\n", encoding="utf-8")

            result = self.run_sanitizer(evidence)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("evidence entry name contains secret-shaped content", result.stderr)
            self.assertNotIn(secret_name, result.stderr)

    def test_workflow_sanitizes_before_conditional_upload(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        validation = workflow.index("test_qemu_evidence_sanitizer.py")
        sanitizer = workflow.index("id: sanitize-evidence")
        command = workflow.index(
            "python3 tools/sanitize-qemu-evidence.py artifacts/qemu-vm-acceptance",
            sanitizer,
        )
        upload = workflow.index("name: Upload non-sensitive VM evidence", command)
        condition = workflow.index(
            "if: ${{ always() && steps.sanitize-evidence.outcome == 'success' }}",
            upload,
        )
        self.assertLess(validation, sanitizer)
        self.assertLess(sanitizer, command)
        self.assertLess(command, upload)
        self.assertLess(upload, condition)
        self.assertIn("      - tools/sanitize-qemu-evidence.py", workflow)
        self.assertIn("      - tests/test_qemu_evidence_sanitizer.py", workflow)


if __name__ == "__main__":
    unittest.main()
