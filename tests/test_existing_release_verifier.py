from __future__ import annotations

import importlib.util
import pathlib
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
VERIFIER_PATH = ROOT / "tools" / "verify-existing-release.py"


def load_verifier():
    spec = importlib.util.spec_from_file_location("existing_release_verifier_bounds", VERIFIER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load existing release verifier")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ExistingReleaseVerifierBoundsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.verifier = load_verifier()

    def test_json_input_is_size_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            path = pathlib.Path(temporary_name) / "manifest.json"
            path.write_bytes(b"{" + b" " * self.verifier.MAX_JSON_BYTES + b"}")
            with self.assertRaisesRegex(SystemExit, "unsafe release manifest size"):
                self.verifier.load_object(path, "release manifest")

    def test_archive_is_size_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            path = pathlib.Path(temporary_name) / "release.tar.gz"
            with path.open("wb") as handle:
                handle.truncate(self.verifier.MAX_ARCHIVE_BYTES + 1)
            with self.assertRaisesRegex(SystemExit, "unsafe release archive size"):
                self.verifier.sha256_file(path)

    def test_symlink_and_hardlink_inputs_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = pathlib.Path(temporary_name)
            target = root / "target.json"
            target.write_text("{}", encoding="utf-8")
            symlink = root / "symlink.json"
            symlink.symlink_to(target)
            with self.assertRaisesRegex(SystemExit, "expected one regular file link"):
                self.verifier.load_object(symlink, "release manifest")

            hardlink = root / "hardlink.json"
            hardlink.hardlink_to(target)
            with self.assertRaisesRegex(SystemExit, "expected one regular file link"):
                self.verifier.load_object(target, "release manifest")


if __name__ == "__main__":
    unittest.main()
