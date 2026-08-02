#!/usr/bin/env python3
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
REVIEWED_COMMIT = "9c38d0095563ea33efd14124babfd29556c0da46"
BOOTSTRAP_BLOB = "eae493681ce5eecd5ea61491f8e08e1f40938e08"
VALIDATOR_BLOB = "2672271aacc0d85013765b3a7887fdec95518643"
DOC_PATHS = (
    ROOT / "README.md",
    ROOT / "SETUP.md",
    ROOT / "CONFIGURATION.md",
    ROOT / "PRODUCTION_READINESS.md",
)


class PrivateRepositoryDocumentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.docs = {
            path.name: path.read_text(encoding="utf-8")
            for path in DOC_PATHS
        }

    def test_private_docs_do_not_use_anonymous_raw_downloads(self) -> None:
        for name, text in self.docs.items():
            with self.subTest(document=name):
                self.assertNotIn(
                    "raw.githubusercontent.com/1-vps/hostpanel",
                    text,
                )
                self.assertNotIn("secretless QEMU", text)
                self.assertNotIn("no GitHub secrets", text)

    def test_all_docs_use_current_release_identifiers(self) -> None:
        for name, text in self.docs.items():
            with self.subTest(document=name):
                self.assertIn(REVIEWED_COMMIT, text)
                self.assertIn("3.4.0", text)

    def test_setup_fetches_exact_private_objects(self) -> None:
        setup = self.docs["SETUP.md"]
        self.assertIn("https://api.github.com/repos/1-vps/hostpanel", setup)
        self.assertIn("application/vnd.github.raw+json", setup)
        self.assertIn("GitHub Contents:Read token", setup)
        self.assertIn(BOOTSTRAP_BLOB, setup)
        self.assertIn(VALIDATOR_BLOB, setup)
        self.assertIn("git hash-object /root/bootstrap-install.sh", setup)
        self.assertIn("git hash-object /root/validate-production-vm.sh", setup)

    def test_setup_uses_transient_git_authentication(self) -> None:
        setup = self.docs["SETUP.md"]
        self.assertIn("export GIT_CONFIG_COUNT=1", setup)
        self.assertIn("http.https://github.com/.extraheader", setup)
        self.assertIn("export GIT_TERMINAL_PROMPT=0", setup)
        self.assertIn("trap cleanup_auth EXIT", setup)
        self.assertIn("unset GH_READ_TOKEN GIT_AUTH_HEADER", setup)
        self.assertIn(
            "unset GIT_CONFIG_COUNT GIT_CONFIG_KEY_0 GIT_CONFIG_VALUE_0 GIT_TERMINAL_PROMPT",
            setup,
        )

    def test_configuration_treats_encoded_header_as_secret(self) -> None:
        configuration = self.docs["CONFIGURATION.md"]
        self.assertIn("Treat `GIT_CONFIG_VALUE_0` as a secret", configuration)
        self.assertIn("Contents: Read-only", configuration)
        self.assertIn("HP_EXPECTED_VERSION=3.4.0", configuration)

    def test_production_guide_requires_blob_evidence(self) -> None:
        production = self.docs["PRODUCTION_READINESS.md"]
        self.assertIn(BOOTSTRAP_BLOB, production)
        self.assertIn(VALIDATOR_BLOB, production)
        self.assertIn("exact Git commit and root-executed blob IDs", production)


if __name__ == "__main__":
    unittest.main()
