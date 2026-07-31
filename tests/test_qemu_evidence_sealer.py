#!/usr/bin/env python3
from __future__ import annotations

import ast
import hashlib
import importlib.util
import pathlib
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
SEALER = ROOT / "tools" / "seal-qemu-evidence.py"
WORKFLOW = ROOT / ".github" / "workflows" / "qemu-vm-acceptance.yml"
MODULE_SPEC = importlib.util.spec_from_file_location("qemu_evidence_sealer", SEALER)
if MODULE_SPEC is None or MODULE_SPEC.loader is None:
    raise RuntimeError("could not load QEMU evidence sealer")
SEALER_MODULE = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(SEALER_MODULE)


def _os_call(node: ast.AST, name: str) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "os"
        and node.func.attr == name
    )


def _contains_os_attribute(node: ast.AST, name: str) -> bool:
    return any(
        isinstance(descendant, ast.Attribute)
        and isinstance(descendant.value, ast.Name)
        and descendant.value.id == "os"
        and descendant.attr == name
        for descendant in ast.walk(node)
    )


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

    def test_fchmod_failure_removes_the_created_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = pathlib.Path(temporary_directory)
            evidence = root / "evidence"
            evidence.mkdir()
            (evidence / "safe.txt").write_text("safe\n", encoding="utf-8")
            archive = root / "evidence.tar"

            with mock.patch.object(
                SEALER_MODULE.os,
                "fchmod",
                side_effect=PermissionError(1, "denied"),
            ):
                with self.assertRaises(PermissionError):
                    SEALER_MODULE.seal_tree(evidence, archive)

            self.assertFalse(archive.exists())

    def test_cleanup_failure_does_not_mask_the_original_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = pathlib.Path(temporary_directory)
            evidence = root / "evidence"
            evidence.mkdir()
            (evidence / "safe.txt").write_text("safe\n", encoding="utf-8")
            archive = root / "evidence.tar"

            with (
                mock.patch.object(
                    SEALER_MODULE.tarfile,
                    "open",
                    side_effect=RuntimeError("primary sealing failure"),
                ),
                mock.patch.object(
                    SEALER_MODULE.os,
                    "unlink",
                    side_effect=OSError(5, "cleanup failure"),
                ),
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "primary sealing failure",
                ):
                    SEALER_MODULE.seal_tree(evidence, archive)

    def test_keyboard_interrupt_removes_the_created_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = pathlib.Path(temporary_directory)
            evidence = root / "evidence"
            evidence.mkdir()
            (evidence / "safe.txt").write_text("safe\n", encoding="utf-8")
            archive = root / "evidence.tar"

            with mock.patch.object(
                SEALER_MODULE.tarfile,
                "open",
                side_effect=KeyboardInterrupt,
            ):
                with self.assertRaises(KeyboardInterrupt):
                    SEALER_MODULE.seal_tree(evidence, archive)

            self.assertFalse(archive.exists())

    def test_archive_creation_invariants_are_structurally_enforced(self) -> None:
        tree = ast.parse(SEALER.read_text(encoding="utf-8"))

        archive_assignments = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and _os_call(node.value, "open")
            and any(keyword.arg == "dir_fd" for keyword in node.value.keywords)
            and _contains_os_attribute(node.value, "O_EXCL")
        ]
        self.assertEqual(len(archive_assignments), 1)
        archive_descriptor = archive_assignments[0].targets[0].id

        seal_tree_function = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "seal_tree"
        )
        seal_tree_nodes = list(ast.walk(seal_tree_function))
        calls = [node for node in seal_tree_nodes if isinstance(node, ast.Call)]
        self.assertTrue(
            any(
                _os_call(call, "fchmod")
                and len(call.args) >= 2
                and isinstance(call.args[0], ast.Name)
                and call.args[0].id == archive_descriptor
                and isinstance(call.args[1], ast.Constant)
                and call.args[1].value == 0o600
                for call in calls
            )
        )
        self.assertTrue(
            any(
                _os_call(call, "fsync")
                and call.args
                and isinstance(call.args[0], ast.Name)
                and call.args[0].id == archive_descriptor
                for call in calls
            )
        )

        path_metadata_assignments = [
            node
            for node in seal_tree_nodes
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and _os_call(node.value, "stat")
            and any(keyword.arg == "dir_fd" for keyword in node.value.keywords)
        ]
        self.assertEqual(len(path_metadata_assignments), 1)
        final_path_assignment = path_metadata_assignments[0]
        final_path_metadata = final_path_assignment.targets[0].id

        descriptor_metadata_assignments = [
            node
            for node in seal_tree_nodes
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and _os_call(node.value, "fstat")
            and node.value.args
            and isinstance(node.value.args[0], ast.Name)
            and node.value.args[0].id == archive_descriptor
            and node.lineno < final_path_assignment.lineno
        ]
        self.assertGreaterEqual(len(descriptor_metadata_assignments), 2)
        final_descriptor_metadata = max(
            descriptor_metadata_assignments,
            key=lambda node: node.lineno,
        ).targets[0].id

        self.assertTrue(
            any(
                isinstance(call.func, ast.Name)
                and call.func.id == "_same_file"
                and len(call.args) == 2
                and isinstance(call.args[0], ast.Name)
                and call.args[0].id == final_descriptor_metadata
                and isinstance(call.args[1], ast.Name)
                and call.args[1].id == final_path_metadata
                for call in calls
            )
        )
        self.assertFalse(
            any(
                isinstance(node, (ast.Import, ast.ImportFrom))
                and any(alias.name == "tempfile" for alias in node.names)
                for node in ast.walk(tree)
            )
        )
        self.assertFalse(any(_os_call(call, "link") for call in calls))

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
