from __future__ import annotations

import hashlib
import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
VERIFIER_PATH = ROOT / "tools" / "verify-source-release-bundle.py"
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


class SourceReleaseBundleVerifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.verifier = load_module("source_bundle_verifier_test", VERIFIER_PATH)
        cls.attestations = load_module("source_bundle_attestations_test", ATTESTATION_PATH)
        cls.builder = load_module("source_bundle_builder_test", BUILDER_PATH)

    @staticmethod
    def sha256(path: pathlib.Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def make_bundle(self, root: pathlib.Path) -> tuple[pathlib.Path, str, str, str]:
        directory = root / "bundle"
        source = root / "source"
        directory.mkdir()
        (source / "app").mkdir(parents=True)
        (source / "VERSION").write_text("3.4.1\n", encoding="utf-8")
        (source / "app" / "main.py").write_text("print('ok')\n", encoding="utf-8")
        (source / "requirements.lock").write_text(
            "example==1.2.3 --hash=sha256:" + "a" * 64 + "\n",
            encoding="utf-8",
        )
        source_version = "3.4.1"
        release_id = "3.4.1-hardened-r1"
        commit = "b" * 40
        timestamp = 1_700_000_000
        identity = self.builder.ReleaseIdentity(source_version, release_id)
        self.attestations.generate_attestations(
            source,
            source_version=source_version,
            release_id=release_id,
            timestamp=timestamp,
        )
        archive = directory / identity.archive_name
        self.builder.write_deterministic_archive(source, archive, identity, timestamp)
        for name in self.attestations.ATTESTATION_NAMES:
            (directory / name).write_bytes((source / name).read_bytes())
        provenance = {
            "archive": {"name": archive.name, "sha256": self.sha256(archive)},
            "baseline": {
                "archive": "hostpanel-v3.4.0-hardened-r5-source.tar.gz",
                "release_id": "3.4.0-hardened-r5",
                "sha256": "c" * 64,
            },
            "built_at": "2023-11-14T22:13:20+00:00",
            "commit": commit,
            "late_overlay_paths": ["README.md"],
            "overlay_paths": ["app"],
            "product": "hostpanel",
            "release_id": release_id,
            "schema": 1,
            "source_version": source_version,
        }
        (directory / self.verifier.PROVENANCE_NAME).write_text(
            json.dumps(provenance, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        payload_names = (
            archive.name,
            self.verifier.PROVENANCE_NAME,
            *self.verifier.ATTESTATION_NAMES,
        )
        (directory / self.verifier.CHECKSUM_NAME).write_text(
            "".join(f"{self.sha256(directory / name)}  {name}\n" for name in payload_names),
            encoding="utf-8",
        )
        return directory, commit, source_version, release_id

    def verify_bundle(
        self,
        directory: pathlib.Path,
        commit: str,
        source_version: str,
        release_id: str,
    ) -> None:
        self.verifier.verify(
            directory,
            ROOT,
            commit,
            source_version,
            release_id,
        )

    def test_valid_bundle_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            directory, commit, source_version, release_id = self.make_bundle(
                pathlib.Path(temporary_name)
            )
            self.verify_bundle(directory, commit, source_version, release_id)

    def test_commit_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            directory, _commit, source_version, release_id = self.make_bundle(
                pathlib.Path(temporary_name)
            )
            with self.assertRaisesRegex(
                self.verifier.BundleVerificationError,
                "provenance commit mismatch",
            ):
                self.verify_bundle(directory, "d" * 40, source_version, release_id)

    def test_checksum_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            directory, commit, source_version, release_id = self.make_bundle(
                pathlib.Path(temporary_name)
            )
            sbom = directory / "sbom.cdx.json"
            sbom.write_bytes(sbom.read_bytes() + b" ")
            with self.assertRaisesRegex(
                self.verifier.BundleVerificationError,
                "checksum mismatch: sbom.cdx.json",
            ):
                self.verify_bundle(directory, commit, source_version, release_id)

    def test_embedded_standalone_difference_is_rejected_even_with_updated_checksum(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            directory, commit, source_version, release_id = self.make_bundle(
                pathlib.Path(temporary_name)
            )
            sbom = directory / "sbom.cdx.json"
            payload = json.loads(sbom.read_text(encoding="utf-8"))
            payload["metadata"]["properties"] = [
                {"name": "fixture", "value": "standalone-drift"}
            ]
            sbom.write_text(
                json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            checksum_path = directory / self.verifier.CHECKSUM_NAME
            lines = []
            for line in checksum_path.read_text(encoding="utf-8").splitlines():
                _digest, name = line.split()
                lines.append(f"{self.sha256(directory / name)}  {name}")
            checksum_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(
                self.verifier.BundleVerificationError,
                "embedded and standalone sbom.cdx.json",
            ):
                self.verify_bundle(directory, commit, source_version, release_id)

    def test_checksum_file_requires_exact_payload_set(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            directory, commit, source_version, release_id = self.make_bundle(
                pathlib.Path(temporary_name)
            )
            checksum_path = directory / self.verifier.CHECKSUM_NAME
            checksum_path.write_text(
                checksum_path.read_text(encoding="utf-8")
                + f"{'e' * 64}  unexpected.json\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                self.verifier.BundleVerificationError,
                "exactly the reviewed payload set",
            ):
                self.verify_bundle(directory, commit, source_version, release_id)

    def test_source_compiles(self) -> None:
        compile(VERIFIER_PATH.read_text(encoding="utf-8"), str(VERIFIER_PATH), "exec")


if __name__ == "__main__":
    unittest.main()
