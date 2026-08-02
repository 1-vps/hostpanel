from __future__ import annotations

import pathlib
import re
import subprocess
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "auto-install.sh"
SETUP = ROOT / "SETUP.md"
CLOUD_INIT = ROOT / "examples" / "cloud-init-hostpanel.yaml"
WORKFLOW = ROOT / ".github" / "workflows" / "automatic-installer.yml"
LAUNCHER_COMMIT = "a88be462efa38e479070b89e0a4c90b4b7b202da"
LAUNCHER_BLOB = "4fa5e025c1516ebaaff260177b572f3253a61aa1"


class AutomaticInstallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = INSTALLER.read_text(encoding="utf-8")
        cls.setup = SETUP.read_text(encoding="utf-8")
        cls.cloud_init = CLOUD_INIT.read_text(encoding="utf-8")
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_shell_syntax_and_noninteractive_help(self) -> None:
        subprocess.run(["bash", "-n", str(INSTALLER)], check=True)
        result = subprocess.run(
            ["bash", str(INSTALLER), "--help"],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("never prompts", result.stdout)
        self.assertIn("HP_GITHUB_TOKEN_FILE", result.stdout)

    def test_all_execution_inputs_are_pinned(self) -> None:
        self.assertIn('QUICK_INSTALL_COMMIT="7745b637d3a77664e385528838800100291d575c"', self.source)
        self.assertIn('QUICK_INSTALL_BLOB="9018696895963da33f4f3dbf3288600a36763455"', self.source)
        self.assertIn('PRODUCT_COMMIT="d50ccea35aa6356f7f815a606fa91f6186b66a6f"', self.source)
        self.assertIn('VALIDATOR_BLOB="2eefb797a50a0a2e2827ca5687ba83a2b4b3eec9"', self.source)
        self.assertNotIn("ref=main", self.source)

    def test_unattended_detection_fails_closed(self) -> None:
        self.assertIn("hostname -f", self.source)
        self.assertIn("SSH_CONNECTION", self.source)
        self.assertIn("Could not detect a valid FQDN", self.source)
        self.assertIn("Could not detect an SSH source", self.source)
        self.assertNotIn("HP_ALLOW_PUBLIC_PANEL=yes", self.source)

    def test_token_handling_is_file_or_descriptor_only(self) -> None:
        self.assertNotRegex(self.source, r"--token\b")
        self.assertNotRegex(self.source.lower(), r"[?&]token=")
        self.assertIn("HP_GITHUB_TOKEN_FILE", self.source)
        self.assertIn("HP_GITHUB_TOKEN_FD", self.source)
        self.assertIn("O_NOFOLLOW", self.source)
        self.assertIn("before.st_nlink != 1", self.source)
        self.assertIn("mode 0400/0600", self.source)
        self.assertIn("/run/secrets/hostpanel_github_token", self.source)

    def test_preflight_is_delegated_to_verified_quick_installer(self) -> None:
        self.assertIn('git hash-object "$WORK_DIR/quick-install.sh"', self.source)
        self.assertIn('HP_GITHUB_TOKEN_FILE="$PRIVATE_TOKEN_FILE" bash "$WORK_DIR/quick-install.sh"', self.source)
        self.assertIn("--yes", self.source)
        self.assertIn("--check-only", self.source)

    def test_idempotence_lock_status_and_validation(self) -> None:
        self.assertIn('flock -n "$LOCK_FD"', self.source)
        self.assertIn('STATUS_DIR="/var/lib/hostpanel"', self.source)
        self.assertIn('STATUS_FILE="$STATUS_DIR/auto-install-status.json"', self.source)
        self.assertIn("Existing HostPanel installation is healthy", self.source)
        self.assertIn("bash /root/validate-production-vm.sh --check", self.source)
        self.assertIn("hostpanel-doctor --quiet", self.source)
        self.assertIn("secrets.token_hex(8)", self.source)

    def test_documented_launcher_is_immutable_and_automatic(self) -> None:
        self.assertIn(LAUNCHER_COMMIT, self.setup)
        self.assertIn(LAUNCHER_BLOB, self.setup)
        self.assertIn(f"auto-install.sh?ref={LAUNCHER_COMMIT}", self.setup)
        self.assertNotIn("auto-install.sh?ref=main", self.setup)
        self.assertIn('printf \'%s\' "$HOSTPANEL_GITHUB_TOKEN" | sudo', self.setup)
        self.assertIn('SSH_CONNECTION="${SSH_CONNECTION:-}"', self.setup)
        self.assertIn("HP_PANEL_DOMAIN=example.com", self.setup)
        self.assertIn('git hash-object "$D/install"', self.setup)
        self.assertIn('exec 3<<<"$T"', self.setup)
        self.assertIn("HP_GITHUB_TOKEN_FD=3", self.setup)
        self.assertNotIn("HP_GITHUB_TOKEN_FILE=/etc/hostpanel/install-github.token bash -c", self.setup)
        self.assertNotIn('cat "$HP_GITHUB_TOKEN_FILE"', self.setup)

    def test_one_line_keeps_secret_out_of_url_and_root_environment(self) -> None:
        one_line = next(
            line
            for line in self.setup.splitlines()
            if line.startswith("printf '%s' \"$HOSTPANEL_GITHUB_TOKEN\" | sudo")
        )
        self.assertNotIn("token=", one_line.lower())
        self.assertNotIn("HOSTPANEL_GITHUB_TOKEN=", one_line)
        self.assertNotIn("Authorization: Bearer $HOSTPANEL_GITHUB_TOKEN", one_line)
        self.assertIn('printf "header = \\"Authorization: Bearer %s\\"\\n" "$T"', one_line)
        self.assertIn("chmod 0600", one_line)
        self.assertIn("rm -f \"$D/curl\"", one_line)

    def test_cloud_init_uses_external_secret_and_pinned_launcher(self) -> None:
        self.assertIn("#cloud-config", self.cloud_init)
        self.assertIn("/run/secrets/hostpanel_github_token", self.cloud_init)
        self.assertIn(f"auto-install.sh?ref={LAUNCHER_COMMIT}", self.cloud_init)
        self.assertNotIn("ref=main", self.cloud_init)
        self.assertNotRegex(self.cloud_init, r"github_pat_[A-Za-z0-9_]+")

    def test_read_only_workflow_checks_automatic_installer(self) -> None:
        self.assertIn("permissions:\n  contents: read", self.workflow)
        self.assertIn("persist-credentials: false", self.workflow)
        self.assertIn("bash -n auto-install.sh", self.workflow)
        self.assertIn("python3 -m unittest -v tests.test_auto_install", self.workflow)
        self.assertRegex(self.workflow, re.compile(r"shellcheck .*auto-install\.sh", re.DOTALL))


if __name__ == "__main__":
    unittest.main()
