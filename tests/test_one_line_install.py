from __future__ import annotations

import pathlib
import re
import subprocess
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
ENTRY = ROOT / "install-one-line.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "automatic-installer.yml"
AUTO_COMMIT = "cb15cca3e1e4d8f2525d7989428c02771bd96331"
AUTO_BLOB = "d3dd590c8e0c673b3fb40e156c01ae0ccf49b43c"


class OneLineInstallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = ENTRY.read_text(encoding="utf-8")
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_shell_syntax_and_help(self) -> None:
        subprocess.run(["bash", "-n", str(ENTRY)], check=True)
        result = subprocess.run(
            ["bash", str(ENTRY), "--help"],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("never prompts", result.stdout)
        self.assertIn("standard input", result.stdout)
        self.assertIn("install-one-line.sh [DOMAIN]", result.stdout)

    def test_entry_is_small_and_pinned_to_the_automatic_engine(self) -> None:
        self.assertLessEqual(len(self.source.splitlines()), 170)
        self.assertIn(f'AUTO_INSTALL_COMMIT="{AUTO_COMMIT}"', self.source)
        self.assertIn(f'AUTO_INSTALL_BLOB="{AUTO_BLOB}"', self.source)
        self.assertNotIn("ref=main", self.source)
        self.assertNotIn("raw.githubusercontent.com", self.source)

    def test_token_is_stdin_only_and_bounded(self) -> None:
        self.assertIn("read_token_from_stdin", self.source)
        self.assertIn("[[ ! -t 0 ]]", self.source)
        self.assertIn("${#TOKEN} <= 512", self.source)
        self.assertRegex(self.source, re.compile(r"\^\[A-Za-z0-9_\]\{20,512\}\$"))
        self.assertNotRegex(self.source, r"--token\b")
        self.assertNotIn("/dev/tty", self.source)
        self.assertNotIn("HP_GITHUB_TOKEN=", self.source)

    def test_private_download_is_https_blob_verified_and_config_free(self) -> None:
        self.assertIn('REPOSITORY_API="https://api.github.com/repos/1-vps/hostpanel"', self.source)
        self.assertIn("curl -q --proto '=https' --tlsv1.2", self.source)
        self.assertIn("application/vnd.github.raw+json", self.source)
        self.assertIn('git hash-object "$WORK_DIR/auto-install.sh"', self.source)
        self.assertIn('rm -f -- "$AUTH_FILE"', self.source)
        self.assertLess(
            self.source.index('rm -f -- "$AUTH_FILE"'),
            self.source.index('bash "$WORK_DIR/auto-install.sh"'),
        )

    def test_secret_handoff_is_scoped_and_descriptor_is_closed(self) -> None:
        self.assertIn('exec 3<<<"$TOKEN"', self.source)
        self.assertIn('HP_GITHUB_TOKEN_FD=3 bash "$WORK_DIR/auto-install.sh"', self.source)
        self.assertIn('exec 3<&-', self.source)
        self.assertNotIn("export HP_GITHUB_TOKEN_FD=3", self.source)
        self.assertNotRegex(self.source.lower(), r"[?&]token=")

    def test_check_only_does_not_install_missing_outer_prerequisites(self) -> None:
        self.assertIn("check-only never installs packages", self.source)
        self.assertIn('check_only="${HP_CHECK_ONLY:-no}"', self.source)

    def test_fail_closed_defaults_and_environment_forwarding(self) -> None:
        self.assertIn('SSH_CONNECTION="${SSH_CONNECTION:-}"', self.source)
        self.assertIn('export HP_PANEL_DOMAIN="$DOMAIN"', self.source)
        self.assertNotIn("HP_ALLOW_PUBLIC_PANEL=yes", self.source)
        self.assertIn("HP_PANEL_ADMIN_CIDR", self.source)
        self.assertIn("HP_CHECK_ONLY=yes", self.source)

    def test_workflow_validates_the_entry_file(self) -> None:
        self.assertGreaterEqual(self.workflow.count("install-one-line.sh"), 4)
        self.assertIn("bash -n install-one-line.sh", self.workflow)
        self.assertIn("tests.test_one_line_install", self.workflow)
        self.assertRegex(
            self.workflow,
            re.compile(r"shellcheck .*auto-install\.sh .*quick-install\.sh .*install-one-line\.sh", re.DOTALL),
        )


if __name__ == "__main__":
    unittest.main()
