from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
ATTESTATION_PATH = ROOT / "tools" / "generate-source-attestations.py"
BUILDER_PATH = ROOT / "tools" / "build-source-release.py"


def load_module(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class SourceAttestationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.attestations = load_module("source_attestations_test", ATTESTATION_PATH)
        cls.builder = load_module("source_attestation_builder_test", BUILDER_PATH)

    @staticmethod
    def lock_text() -> str:
        return (
            "# generated fixture\n"
            "Example_Pkg==2.0.0 \\\n"
            "    --hash=sha256:" + "b" * 64 + " \\\n"
            "    --hash=sha256:" + "a" * 64 + "\n"
            "alpha==1.0.0 ; python_version >= '3.12' \\\n"
            "    --hash=sha256:" + "c" * 64 + "\n"
        )

    def make_source_tree(self, parent: pathlib.Path) -> pathlib.Path:
        source = parent / "source"
        (source / "app").mkdir(parents=True)
        (source / "empty").mkdir()
        (source / "requirements.lock").write_text(self.lock_text(), encoding="utf-8")
        (source / "app" / "main.py").write_text("print('ok')\n", encoding="utf-8")
        (source / "VERSION").write_text("3.4.1\n", encoding="utf-8")
        return source

    def test_hashed_lock_is_parsed_canonically(self) -> None:
        packages = self.attestations.parse_requirements_lock(
            self.lock_text().encode("utf-8")
        )
        self.assertEqual([package.name for package in packages], ["alpha", "example-pkg"])
        self.assertEqual(packages[0].marker, "python_version >= '3.12'")
        self.assertEqual(packages[1].hashes, ("a" * 64, "b" * 64))
        self.assertEqual(packages[1].purl, "pkg:pypi/example-pkg@2.0.0")

    def test_lock_rejects_duplicates_and_unhashed_entries(self) -> None:
        duplicate = (
            "Example_Pkg==1.0 --hash=sha256:" + "a" * 64 + "\n"
            "example-pkg==1.0 --hash=sha256:" + "b" * 64 + "\n"
        )
        with self.assertRaisesRegex(self.attestations.AttestationError, "duplicate"):
            self.attestations.parse_requirements_lock(duplicate.encode("utf-8"))
        with self.assertRaisesRegex(self.attestations.AttestationError, "no SHA-256"):
            self.attestations.parse_requirements_lock(b"example==1.0\n")

    def test_generated_attestations_are_deterministic_and_self_verifying(self) -> None:
        with tempfile.TemporaryDirectory() as first_name, tempfile.TemporaryDirectory() as second_name:
            first = self.make_source_tree(pathlib.Path(first_name))
            second = self.make_source_tree(pathlib.Path(second_name))
            for source in (first, second):
                self.attestations.generate_attestations(
                    source,
                    source_version="3.4.1",
                    release_id="3.4.1-hardened-r1",
                    timestamp=1_700_000_000,
                )
                self.attestations.verify_tree_attestations(
                    source,
                    source_version="3.4.1",
                    release_id="3.4.1-hardened-r1",
                    timestamp=1_700_000_000,
                )
            for name in self.attestations.ATTESTATION_NAMES:
                self.assertEqual((first / name).read_bytes(), (second / name).read_bytes())

            manifest = json.loads(
                (first / self.attestations.FILE_MANIFEST_NAME).read_text(encoding="utf-8")
            )
            paths = {entry["path"] for entry in manifest["entries"]}
            self.assertNotIn(self.attestations.FILE_MANIFEST_NAME, paths)
            self.assertIn(self.attestations.SBOM_NAME, paths)
            self.assertIn(self.attestations.LOCK_ATTESTATION_NAME, paths)
            self.assertIn("empty", paths)
            sbom = json.loads((first / self.attestations.SBOM_NAME).read_text(encoding="utf-8"))
            self.assertEqual(sbom["bomFormat"], "CycloneDX")
            self.assertEqual(sbom["specVersion"], "1.6")
            self.assertEqual(len(sbom["components"]), 2)

    def test_tree_verification_detects_payload_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            source = self.make_source_tree(pathlib.Path(temporary_name))
            self.attestations.generate_attestations(
                source,
                source_version="3.4.1",
                release_id="3.4.1-hardened-r1",
                timestamp=1_700_000_000,
            )
            (source / "app" / "main.py").write_text("print('changed')\n", encoding="utf-8")
            with self.assertRaisesRegex(
                self.attestations.AttestationError,
                "file manifest does not match",
            ):
                self.attestations.verify_tree_attestations(
                    source,
                    source_version="3.4.1",
                    release_id="3.4.1-hardened-r1",
                    timestamp=1_700_000_000,
                )

    def test_archive_attestations_match_archive_members(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = pathlib.Path(temporary_name)
            source = self.make_source_tree(root)
            timestamp = 1_700_000_000
            identity = self.builder.ReleaseIdentity("3.4.1", "3.4.1-hardened-r1")
            self.attestations.generate_attestations(
                source,
                source_version=identity.source_version,
                release_id=identity.release_id,
                timestamp=timestamp,
            )
            archive = root / identity.archive_name
            self.builder.write_deterministic_archive(source, archive, identity, timestamp)
            self.attestations.verify_archive_attestations(
                archive,
                root_name=identity.archive_root,
                source_version=identity.source_version,
                release_id=identity.release_id,
            )
            output = root / "dist"
            output.mkdir()
            self.attestations.copy_attestations(source, output)
            for name in self.attestations.ATTESTATION_NAMES:
                self.assertEqual((source / name).read_bytes(), (output / name).read_bytes())

    def test_source_compiles(self) -> None:
        compile(
            ATTESTATION_PATH.read_text(encoding="utf-8"),
            str(ATTESTATION_PATH),
            "exec",
        )


if __name__ == "__main__":
    unittest.main()
