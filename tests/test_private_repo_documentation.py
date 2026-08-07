#!/usr/bin/env python3
import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
AUTO_COMMIT = "1a86d380e7ebab287c767d183013b599cb116f7f"
AUTO_BLOB = "db23963e101b9194994da2ff8077b40a6b1cb99c"
PRODUCT_COMMIT = "755dcd5e47b7c82404b267e8df4dec27626fe341"
DOC_PATHS = (
    ROOT / "README.md",
    ROOT / "SETUP.md",
    ROOT / "CONFIGURATION.md",
    ROOT / "PRODUCTION_READINESS.md",
)


class PrivateRepositoryDocumentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.docs = {path.name: path.read_text(encoding="utf-8") for path in DOC_PATHS}

    def test_private_docs_do_not_use_anonymous_raw_downloads(self) -> None:
        for name, text in self.docs.items():
            with self.subTest(document=name):
                self.assertNotIn("raw.githubusercontent.com/1-vps/hostpanel", text)
                self.assertNotIn("?ref=main", text)

    def test_all_docs_reference_current_deployable_version(self) -> None:
        for name, text in self.docs.items():
            with self.subTest(document=name):
                self.assertIn("3.4.1", text)

    def test_setup_documents_exact_automatic_installer(self) -> None:
        setup = self.docs["SETUP.md"]
        self.assertIn("`auto-install.sh` is the only documented HostPanel installation entry point", setup)
        self.assertIn(AUTO_COMMIT, setup)
        self.assertIn(AUTO_BLOB, setup)
        self.assertIn("authenticated checkout or", setup)
        self.assertIn("reviewed file transfer", setup)
        self.assertIn("Do not execute a moving `main` branch as root", setup)
        self.assertNotIn("quick-install.sh", setup)
        self.assertNotIn("install-one-line.sh", setup)

    def test_setup_documents_bounded_private_authentication(self) -> None:
        setup = self.docs["SETUP.md"]
        self.assertIn("Contents: Read-only", setup)
        self.assertIn("HP_GITHUB_TOKEN_FILE", setup)
        self.assertIn("HP_GITHUB_TOKEN_FD=3", setup)
        self.assertIn("root-owned", setup)
        self.assertRegex(setup, re.compile(r"mode `0400` or\s+`0600`"))
        self.assertIn("not placed in", setup)
        self.assertIn("URL", setup)
        self.assertIn("normal command argument", setup)

    def test_configuration_matches_current_immutable_chain(self) -> None:
        configuration = self.docs["CONFIGURATION.md"]
        self.assertIn(AUTO_COMMIT, configuration)
        self.assertIn(AUTO_BLOB, configuration)
        self.assertIn(PRODUCT_COMMIT, configuration)
        self.assertIn("Contents: Read-only", configuration)
        self.assertIn("Operators do not select a moving `HP_REPO_REF`", configuration)
        self.assertNotIn("9c38d0095563ea33efd14124babfd29556c0da46", configuration)
        self.assertNotIn("3.4.0-hardened-r6", configuration)

    def test_production_guide_matches_current_immutable_chain(self) -> None:
        production = self.docs["PRODUCTION_READINESS.md"]
        self.assertIn(AUTO_COMMIT, production)
        self.assertIn(AUTO_BLOB, production)
        self.assertIn(PRODUCT_COMMIT, production)
        self.assertIn("HP_EXPECTED_VERSION=3.4.1", production)
        self.assertIn("exact Git commit and root-executed blob IDs", production)
        self.assertNotIn("9c38d0095563ea33efd14124babfd29556c0da46", production)
        self.assertNotIn("HP_EXPECTED_VERSION=3.4.0", production)


if __name__ == "__main__":
    unittest.main()
