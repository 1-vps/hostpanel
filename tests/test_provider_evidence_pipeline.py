from __future__ import annotations

import importlib.util
import os
import pathlib
import stat
import sys
import tarfile
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
PREPARER_PATH = ROOT / "tools" / "prepare-provider-evidence.py"
SANITIZER_PATH = ROOT / "tools" / "sanitize-provider-evidence.py"
SEALER_PATH = ROOT / "tools" / "seal-provider-evidence.py"


def load_module(path: pathlib.Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ProviderEvidencePipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.preparer = load_module(PREPARER_PATH, "provider_evidence_preparer_test")
        cls.sanitizer = load_module(SANITIZER_PATH, "provider_evidence_sanitizer_test")
        cls.sealer = load_module(SEALER_PATH, "provider_evidence_sealer_test")

    def test_empty_tree_gets_private_fail_closed_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            previous = pathlib.Path.cwd()
            os.chdir(temporary_name)
            try:
                self.preparer.prepare()
                root = pathlib.Path("provider-artifacts/evidence")
                marker = root / "runner-evidence-state.txt"
                self.assertTrue(marker.is_file())
                self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)
                self.assertEqual(stat.S_IMODE(marker.stat().st_mode), 0o600)
                self.assertEqual(marker.stat().st_nlink, 1)
                self.assertIn("No provider evidence", marker.read_text(encoding="utf-8"))
            finally:
                os.chdir(previous)

    def test_secrets_are_redacted_before_deterministic_sealing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            parent = pathlib.Path(temporary_name)
            root = parent / "evidence"
            root.mkdir(mode=0o700)
            evidence = root / "validator.txt"
            evidence.write_bytes(
                b"url=https://operator:supersecret@example.invalid/path\n"
                b"Authorization: Bearer top-secret-value\n"
                b"token=github_pat_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456\n"
                b"-----BEGIN PRIVATE KEY-----\nprivate\n-----END PRIVATE KEY-----\n"
            )
            os.chmod(evidence, 0o600)

            file_count, changed_count = self.sanitizer.sanitize_tree(root)
            self.assertEqual((file_count, changed_count), (1, 1))
            sanitized = evidence.read_bytes()
            self.assertNotIn(b"supersecret", sanitized)
            self.assertNotIn(b"top-secret-value", sanitized)
            self.assertNotIn(b"github_pat_", sanitized)
            self.assertNotIn(b"BEGIN PRIVATE KEY", sanitized)
            self.assertIn(b"[REDACTED]", sanitized)

            first = parent / "first.tar"
            second = parent / "second.tar"
            self.sealer.seal_tree(root, first)
            self.sealer.seal_tree(root, second)
            self.assertEqual(first.read_bytes(), second.read_bytes())

            with tarfile.open(first, "r:") as archive:
                members = archive.getmembers()
                self.assertEqual(
                    [member.name for member in members],
                    ["hostpanel-vps-acceptance/validator.txt"],
                )
                member = members[0]
                self.assertTrue(member.isreg())
                self.assertEqual(member.mode, 0o600)
                self.assertEqual(member.uid, 0)
                self.assertEqual(member.gid, 0)
                self.assertEqual(member.mtime, 0)
                extracted = archive.extractfile(member)
                self.assertIsNotNone(extracted)
                assert extracted is not None
                self.assertEqual(extracted.read(), sanitized)

    def test_symlinks_and_hardlinks_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            parent = pathlib.Path(temporary_name)
            root = parent / "evidence"
            root.mkdir()
            target = root / "target.txt"
            target.write_text("safe\n", encoding="utf-8")
            symlink = root / "link.txt"
            symlink.symlink_to(target.name)
            with self.assertRaisesRegex(RuntimeError, "unsafe evidence file"):
                self.sanitizer.sanitize_tree(root)

            symlink.unlink()
            hardlink = root / "hardlink.txt"
            os.link(target, hardlink)
            with self.assertRaisesRegex(RuntimeError, "multiple links"):
                self.sanitizer.sanitize_tree(root)

    def test_provider_wrappers_compile(self) -> None:
        for path in (PREPARER_PATH, SANITIZER_PATH, SEALER_PATH):
            compile(path.read_text(encoding="utf-8"), str(path), "exec")


if __name__ == "__main__":
    unittest.main()
