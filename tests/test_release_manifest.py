from __future__ import annotations

import copy
import hashlib
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import validate_release_manifest as release

TEST_PRIVATE_KEY = b"""-----BEGIN PRIVATE KEY-----\nMC4CAQAwBQYDK2VwBCIEIB7pjW954i41t4D5AZuW5mR9gIZuWOvlqrt0RkE9aIVe\n-----END PRIVATE KEY-----\n"""
TEST_PUBLIC_KEY = b"""-----BEGIN PUBLIC KEY-----\nMCowBQYDK2VwAyEA+KtLBsTyBCLkQeVYKhDCXcz6WUplSXq/2/+7H6htDts=\n-----END PUBLIC KEY-----\n"""


@unittest.skipUnless(shutil.which("openssl"), "openssl is required")
class ReleaseManifestTests(unittest.TestCase):
    def sign_checksums(self, root: pathlib.Path) -> None:
        private = root / ".test-release-private.pem"
        private.write_bytes(TEST_PRIVATE_KEY)
        subprocess.run(
            [
                shutil.which("openssl") or "openssl",
                "pkeyutl",
                "-sign",
                "-inkey",
                str(private),
                "-rawin",
                "-in",
                str(root / "SHA256SUMS"),
                "-out",
                str(root / "SHA256SUMS.sig"),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        private.unlink()

    def build_repository(self, root: pathlib.Path) -> dict[str, object]:
        archive_name = "hostpanel-v3.4.0-hardened-r5-source.tar.gz"
        archive = root / archive_name
        archive.write_bytes(b"signed-source-test\n")
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        (root / "SHA256SUMS").write_text(
            f"{digest}  {archive_name}\n",
            encoding="utf-8",
        )
        self.sign_checksums(root)
        (root / "hostpanel-v3.4.0-hardened-r5-release.pub").write_bytes(TEST_PUBLIC_KEY)
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
                "blockers": [{"id": "test-gate", "description": "Test blocker"}],
            },
            "source_artifacts": {
                "archive": archive_name,
                "checksum_manifest": "SHA256SUMS",
                "signature": "SHA256SUMS.sig",
            },
            "platform": {
                "architectures": ["x86_64", "aarch64"],
                "operating_systems": [
                    "Ubuntu 22.04",
                    "Ubuntu 24.04",
                    "Ubuntu 26.04",
                    "Debian 12",
                    "Debian 13",
                    "Rocky Linux 9",
                    "Rocky Linux 10",
                    "AlmaLinux 9",
                    "AlmaLinux 10",
                ],
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
        (root / "README.md").write_text(
            markers
            + "Current deployable release: **3.4.1**\n"
            + "Authenticated signed base: **3.4.0-hardened-r5**\n"
            + "Release channel: **candidate**\n"
            + "Production publication: **blocked**\n"
            + "A deployable build is not the same as an approved production publication.\n"
            + "- Ubuntu 22.04, 24.04, or 26.04\n"
            + "- Debian 12 or 13\n"
            + "- Rocky Linux 9 or 10\n"
            + "- AlmaLinux 9 or 10\n"
            + "- x86-64/AMD64 or ARM64/AArch64\n"
            + "- at least 2 GiB RAM and 10 GiB free on `/`\n"
            + "Installed application version: `3.4.1`\n",
            encoding="utf-8",
        )
        (root / "CONFIGURATION.md").write_text(
            markers
            + "This reference describes HostPanel `3.4.1`,\n"
            + "derived from signed base **3.4.0-hardened-r5**.\n"
            + "Installed `/opt/hostpanel/VERSION`: `3.4.1`\n",
            encoding="utf-8",
        )
        (root / "PRODUCTION_READINESS.md").write_text(
            markers
            + "This checklist applies to deployable HostPanel release **3.4.1**, derived from\n"
            + "signed base **3.4.0-hardened-r5**. It does not itself authorize production\n"
            + "publication.\ninstalled version       3.4.1\n",
            encoding="utf-8",
        )
        return manifest

    @staticmethod
    def write_manifest(root: pathlib.Path, manifest: dict[str, object]) -> None:
        (root / "RELEASE-MANIFEST.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )

    def validate(self, root: pathlib.Path):
        return release.validate_repository(
            root, trusted_release_public_key=TEST_PUBLIC_KEY
        )

    def test_valid_repository_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            self.build_repository(root)
            result = self.validate(root)
            self.assertEqual(result[0], "3.4.1")
            self.assertEqual(result[4], "hostpanel-v3.4.0-hardened-r5-source.tar.gz")
            self.assertRegex(result[5], r"^[0-9a-f]{64}$")

    def test_checksum_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            self.build_repository(root)
            (root / "hostpanel-v3.4.0-hardened-r5-source.tar.gz").write_bytes(b"tampered\n")
            with self.assertRaisesRegex(release.ValidationError, "checksum mismatch"):
                self.validate(root)

    def test_checksum_signature_is_verified_cryptographically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            self.build_repository(root)
            (root / "SHA256SUMS.sig").write_bytes(b"x" * 64)
            with self.assertRaisesRegex(release.ValidationError, "signature verification failed"):
                self.validate(root)

    def test_modified_checksum_manifest_with_stale_signature_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            self.build_repository(root)
            with (root / "SHA256SUMS").open("a", encoding="utf-8") as handle:
                handle.write("0" * 64 + "  unrelated.txt\n")
            with self.assertRaisesRegex(release.ValidationError, "signature verification failed"):
                self.validate(root)

    def test_second_source_tarball_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            self.build_repository(root)
            other = "hostpanel-v9.9.9-hardened-r1-source.tar.gz"
            with (root / "SHA256SUMS").open("a", encoding="utf-8") as handle:
                handle.write("0" * 64 + f"  {other}\n")
            self.sign_checksums(root)
            with self.assertRaisesRegex(release.ValidationError, "exactly one HostPanel source tarball"):
                self.validate(root)

    def test_duplicate_archive_checksum_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            self.build_repository(root)
            checksums = root / "SHA256SUMS"
            checksums.write_text(checksums.read_text(encoding="utf-8") * 2, encoding="utf-8")
            self.sign_checksums(root)
            with self.assertRaisesRegex(release.ValidationError, "exactly one HostPanel source tarball"):
                self.validate(root)

    def test_artifact_path_traversal_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            manifest = self.build_repository(root)
            modified = copy.deepcopy(manifest)
            modified["source_artifacts"]["checksum_manifest"] = "../SHA256SUMS"
            self.write_manifest(root, modified)
            with self.assertRaisesRegex(release.ValidationError, "safe repository-root filename"):
                self.validate(root)

    def test_archive_name_must_match_signed_base(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            manifest = self.build_repository(root)
            modified = copy.deepcopy(manifest)
            modified["source_artifacts"]["archive"] = "other.tar.gz"
            self.write_manifest(root, modified)
            with self.assertRaisesRegex(release.ValidationError, "must match release.signed_base"):
                self.validate(root)

    def test_short_signature_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            self.build_repository(root)
            (root / "SHA256SUMS.sig").write_bytes(b"short")
            with self.assertRaisesRegex(release.ValidationError, "64-byte raw signature"):
                self.validate(root)

    def test_release_public_key_must_match_embedded_trust_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            self.build_repository(root)
            (root / "hostpanel-v3.4.0-hardened-r5-release.pub").write_text("wrong\n")
            with self.assertRaisesRegex(release.ValidationError, "embedded trust root"):
                self.validate(root)

    def test_release_version_symlink_is_rejected_without_disclosure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            self.build_repository(root)
            secret = root / "secret.txt"
            secret.write_text("DO-NOT-LEAK\n")
            (root / "RELEASE_VERSION").unlink()
            (root / "RELEASE_VERSION").symlink_to(secret)
            try:
                self.validate(root)
            except release.ValidationError as exc:
                self.assertNotIn("DO-NOT-LEAK", str(exc))
                self.assertIn("safely open", str(exc))
            else:
                self.fail("symlinked RELEASE_VERSION was accepted")

    def test_manifest_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            self.build_repository(root)
            original = root / "manifest-real.json"
            (root / "RELEASE-MANIFEST.json").replace(original)
            (root / "RELEASE-MANIFEST.json").symlink_to(original)
            with self.assertRaisesRegex(release.ValidationError, "safely open"):
                self.validate(root)

    def test_authoritative_document_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            self.build_repository(root)
            original = root / "README.real"
            (root / "README.md").replace(original)
            (root / "README.md").symlink_to(original)
            with self.assertRaisesRegex(release.ValidationError, "safely open"):
                self.validate(root)

    def test_authoritative_document_list_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            manifest = self.build_repository(root)
            modified = copy.deepcopy(manifest)
            modified["authoritative_documents"] = ["README.md", "RELEASE_VERSION"]
            self.write_manifest(root, modified)
            with self.assertRaisesRegex(release.ValidationError, "reviewed maintained set"):
                self.validate(root)

    def test_visible_readme_release_declaration_is_validated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            self.build_repository(root)
            readme = root / "README.md"
            readme.write_text(
                readme.read_text().replace(
                    "Current deployable release: **3.4.1**",
                    "Current deployable release: **3.4.0**",
                )
            )
            with self.assertRaisesRegex(release.ValidationError, "visible release version"):
                self.validate(root)

    def test_visible_signed_base_declaration_is_validated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            self.build_repository(root)
            config = root / "CONFIGURATION.md"
            config.write_text(config.read_text().replace("3.4.0-hardened-r5", "3.4.0-hardened-r4"))
            with self.assertRaisesRegex(release.ValidationError, "authoritative marker|visible release/base"):
                self.validate(root)

    def test_visible_platform_declaration_is_validated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            self.build_repository(root)
            readme = root / "README.md"
            readme.write_text(
                readme.read_text().replace(
                    "- Ubuntu 22.04, 24.04, or 26.04",
                    "- Ubuntu 22.04, 24.04, or 25.10",
                )
            )
            with self.assertRaisesRegex(
                release.ValidationError, "Ubuntu platform versions"
            ):
                self.validate(root)

    def test_extra_visible_platform_version_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            self.build_repository(root)
            readme = root / "README.md"
            readme.write_text(
                readme.read_text().replace(
                    "- Ubuntu 22.04, 24.04, or 26.04",
                    "- Ubuntu 22.04, 24.04, 25.10, or 26.04",
                )
            )
            with self.assertRaisesRegex(
                release.ValidationError, "Ubuntu platform versions"
            ):
                self.validate(root)

    def test_manifest_status_change_requires_visible_contract_update(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            manifest = self.build_repository(root)
            modified = copy.deepcopy(manifest)
            modified["release"]["status"] = "withdrawn"
            self.write_manifest(root, modified)
            for name in ("README.md", "CONFIGURATION.md", "PRODUCTION_READINESS.md"):
                path = root / name
                path.write_text(
                    path.read_text().replace(
                        "{{HOSTPANEL_RELEASE_STATUS}}=deployable-not-publishable",
                        "{{HOSTPANEL_RELEASE_STATUS}}=withdrawn",
                    )
                )
            with self.assertRaisesRegex(
                release.ValidationError, "visible release-status contract"
            ):
                self.validate(root)

    def test_conflicting_document_version_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            self.build_repository(root)
            with (root / "README.md").open("a", encoding="utf-8") as handle:
                handle.write("Installed application version: `3.4.0`\n")
            with self.assertRaisesRegex(release.ValidationError, "conflicting installed version"):
                self.validate(root)


if __name__ == "__main__":
    unittest.main()
