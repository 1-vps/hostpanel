from __future__ import annotations

import copy
import hashlib
import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import validate_release_manifest as release


class ReleaseManifestTests(unittest.TestCase):
    def build_repository(self, root: pathlib.Path) -> dict[str, object]:
        archive_name = "hostpanel-v3.4.0-hardened-r5-source.tar.gz"
        archive = root / archive_name
        archive.write_bytes(b"signed-source-test\n")
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        (root / "SHA256SUMS").write_text(
            f"{digest}  {archive_name}\n",
            encoding="utf-8",
        )
        (root / "SHA256SUMS.sig").write_bytes(b"s" * 64)
        (root / "RELEASE_VERSION").write_text("3.4.1\n", encoding="utf-8")
        manifest: dict[str, object] = {
            "schema_version": 1,
            "product": "HostPanel",
            "release": {
                "version": "3.4.1",
                "channel": "candidate",
                "status": "deployable-not-publishable",
                "signed_base": "3.4.0-hardened-r5",
                "production_publish_allowed": False,
                "blockers": [
                    {
                        "id": "test-gate",
                        "description": "Test blocker",
                    }
                ],
            },
            "source_artifacts": {
                "archive": archive_name,
                "checksum_manifest": "SHA256SUMS",
                "signature": "SHA256SUMS.sig",
            },
            "platform": {
                "architectures": ["x86_64"],
                "operating_systems": ["Ubuntu 24.04"],
                "minimum_ram_mib": 2048,
                "minimum_root_free_mib": 10240,
            },
            "authoritative_documents": [
                "README.md",
                "CONFIGURATION.md",
                "PRODUCTION_READINESS.md",
                "RELEASE_VERSION",
            ],
            "historical_documents": [],
        }
        self.write_manifest(root, manifest)
        markers = (
            "{{HOSTPANEL_RELEASE_VERSION}}=3.4.1\n"
            "{{HOSTPANEL_SIGNED_BASE}}=3.4.0-hardened-r5\n"
            "{{HOSTPANEL_RELEASE_STATUS}}=deployable-not-publishable\n"
            "{{HOSTPANEL_PUBLICATION_ALLOWED}}=false\n"
        )
        for name in release.MAINTAINED_DOC_NAMES:
            (root / name).write_text(markers, encoding="utf-8")
        return manifest

    @staticmethod
    def write_manifest(
        root: pathlib.Path,
        manifest: dict[str, object],
    ) -> None:
        (root / "RELEASE-MANIFEST.json").write_text(
            json.dumps(manifest, indent=2) + "\n",
            encoding="utf-8",
        )

    def test_valid_repository_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            self.build_repository(root)
            result = release.validate_repository(root)
            self.assertEqual(result[0], "3.4.1")
            self.assertEqual(
                result[4],
                "hostpanel-v3.4.0-hardened-r5-source.tar.gz",
            )
            self.assertRegex(result[5], r"^[0-9a-f]{64}$")

    def test_checksum_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            self.build_repository(root)
            (root / "hostpanel-v3.4.0-hardened-r5-source.tar.gz").write_bytes(
                b"tampered\n"
            )
            with self.assertRaisesRegex(
                release.ValidationError,
                "checksum mismatch",
            ):
                release.validate_repository(root)

    def test_duplicate_archive_checksum_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            self.build_repository(root)
            checksums = root / "SHA256SUMS"
            checksums.write_text(
                checksums.read_text(encoding="utf-8") * 2,
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                release.ValidationError,
                "exactly one entry",
            ):
                release.validate_repository(root)

    def test_artifact_path_traversal_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            manifest = self.build_repository(root)
            modified = copy.deepcopy(manifest)
            modified["source_artifacts"]["checksum_manifest"] = "../SHA256SUMS"
            self.write_manifest(root, modified)
            with self.assertRaisesRegex(
                release.ValidationError,
                "safe repository-root filename",
            ):
                release.validate_repository(root)

    def test_archive_name_must_match_signed_base(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            manifest = self.build_repository(root)
            modified = copy.deepcopy(manifest)
            modified["source_artifacts"]["archive"] = "other.tar.gz"
            self.write_manifest(root, modified)
            with self.assertRaisesRegex(
                release.ValidationError,
                "must match release.signed_base",
            ):
                release.validate_repository(root)

    def test_short_signature_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            self.build_repository(root)
            (root / "SHA256SUMS.sig").write_bytes(b"short")
            with self.assertRaisesRegex(
                release.ValidationError,
                "64-byte raw signature",
            ):
                release.validate_repository(root)

    def test_conflicting_document_version_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            self.build_repository(root)
            with (root / "README.md").open("a", encoding="utf-8") as handle:
                handle.write("Installed application version: `3.4.0`\n")
            with self.assertRaisesRegex(
                release.ValidationError,
                "conflicting installed version",
            ):
                release.validate_repository(root)


if __name__ == "__main__":
    unittest.main()
