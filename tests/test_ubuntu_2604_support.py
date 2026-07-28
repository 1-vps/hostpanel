#!/usr/bin/env python3
import os
import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def generate_installer() -> str:
    with tempfile.TemporaryDirectory() as directory:
        output = pathlib.Path(directory) / "install.generated.sh"
        result = subprocess.run(
            [
                "python3",
                str(ROOT / "tools" / "harden_install.py"),
                str(ROOT / "install.base.sh"),
                str(output),
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        if result.returncode != 0:
            raise AssertionError(result.stdout)
        syntax = subprocess.run(
            ["bash", "-n", str(output)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        if syntax.returncode != 0:
            raise AssertionError(syntax.stdout)
        return output.read_text(encoding="utf-8")


class Ubuntu2604InstallerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.installer = generate_installer()

    def test_generated_support_and_repository_policy(self):
        self.assertIn('"$VERSION_ID" == 26.04', self.installer)
        self.assertIn("Ubuntu 22.04/24.04/26.04", self.installer)
        self.assertIn('MULTI_PHP_REPO_MODE="${HP_MULTI_PHP_REPO:-off}"', self.installer)
        self.assertIn('RSPAMD_REPO_MODE="${HP_RSPAMD_REPO:-off}"', self.installer)
        self.assertIn(
            "debian:13|debian:13.*|ubuntu:26.04|ubuntu:26.04.*",
            self.installer,
        )
        self.assertNotIn("https://repo.litespeed.sh", self.installer)

    def test_setup_and_matrix_are_consistent(self):
        setup = (ROOT / "SETUP.md").read_text(encoding="utf-8")
        matrix = (ROOT / "test-matrix.sh").read_text(encoding="utf-8")
        full_matrix = (ROOT / "run-full-test-matrix.sh").read_text(encoding="utf-8")
        self.assertIn("Ubuntu 22.04, 24.04, or 26.04", setup)
        self.assertIn("distribution-provided PHP 8.5", setup)
        for content in (matrix, full_matrix):
            self.assertIn('ubuntu-26.04]="ubuntu:26.04"', content)

    def run_preflight(self, version: str):
        with tempfile.TemporaryDirectory() as directory:
            release = pathlib.Path(directory) / "os-release"
            release.write_text(
                f'ID=ubuntu\nVERSION_ID="{version}"\nPRETTY_NAME="Ubuntu {version} LTS"\nVERSION_CODENAME=resolute\n',
                encoding="utf-8",
            )
            env = os.environ.copy()
            env.update(
                HP_OS_RELEASE_FILE=str(release),
                HP_PANEL_HOST="panel.example.test",
                HP_ALLOW_EXISTING_STACK="yes",
                HP_MULTI_PHP_REPO="off",
                HP_RSPAMD_REPO="off",
            )
            return subprocess.run(
                ["bash", str(ROOT / "install.sh"), "--check", "--role", "web"],
                cwd=ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

    def test_ubuntu_2604_preflight_passes(self):
        result = self.run_preflight("26.04")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("Preflight passed", result.stdout)

    def test_unsupported_interim_release_is_still_rejected(self):
        result = self.run_preflight("25.10")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("Unsupported OS release", result.stdout)


if __name__ == "__main__":
    unittest.main()
