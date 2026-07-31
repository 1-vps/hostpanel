#!/usr/bin/env python3
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "vps-acceptance.yml"


class VPSAcceptanceWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_is_manual_and_environment_gated(self) -> None:
        self.assertIn("workflow_dispatch:", self.text)
        self.assertNotIn("pull_request:", self.text)
        self.assertNotIn("push:\n", self.text)
        self.assertIn("environment: vps-acceptance", self.text)
        self.assertIn("ERASE-AND-INSTALL", self.text)
        self.assertIn("VPS_PROVIDER_SNAPSHOT_CONFIRMED", self.text)

    def test_credentials_are_secret_backed(self) -> None:
        self.assertIn("VPS_ROOT_PASSWORD: ${{ secrets.VPS_ROOT_PASSWORD }}", self.text)
        self.assertIn("VPS_SSH_KNOWN_HOSTS: ${{ secrets.VPS_SSH_KNOWN_HOSTS }}", self.text)
        self.assertIn("SSHPASS: ${{ secrets.VPS_ROOT_PASSWORD }}", self.text)
        self.assertNotIn("inputs.password", self.text)
        self.assertNotRegex(self.text, r"(?m)^\s+password:\s*$")

    def test_ssh_is_fail_closed(self) -> None:
        self.assertIn("StrictHostKeyChecking=yes", self.text)
        self.assertIn("UserKnownHostsFile=", self.text)
        self.assertIn("PreferredAuthentications=password", self.text)
        self.assertNotIn("StrictHostKeyChecking=no", self.text)
        self.assertNotIn("UserKnownHostsFile=/dev/null", self.text)

    def test_install_is_pinned_and_preserves_evidence(self) -> None:
        self.assertIn("9c38d0095563ea33efd14124babfd29556c0da46", self.text)
        self.assertIn("EXPECTED_VERSION: 3.4.0", self.text)
        self.assertNotIn("EXPECTED_VERSION: 3.4.0-hardened-r6", self.text)
        self.assertIn("validate-production-vm.sh --prepare-reboot", self.text)
        self.assertIn("validate-production-vm.sh --post-reboot", self.text)
        self.assertIn("actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02", self.text)
        self.assertNotIn("eval ", self.text)

    def test_private_repository_auth_is_transient(self) -> None:
        token_lines = [line for line in self.text.splitlines() if "HP_VPS_REPO_TOKEN:" in line]
        self.assertEqual(token_lines, ["          HP_VPS_REPO_TOKEN: ${{ github.token }}"])
        self.assertIn("REPO_AUTH_HEADER=", self.text)
        self.assertIn("export GIT_CONFIG_COUNT=1", self.text)
        self.assertIn("clear_repo_auth(){", self.text)
        self.assertIn("sed -i '/^export GIT_/d' /root/hostpanel-acceptance.env", self.text)
        self.assertIn(
            "unset GIT_CONFIG_COUNT GIT_CONFIG_KEY_0 GIT_CONFIG_VALUE_0 GIT_TERMINAL_PROMPT",
            self.text,
        )
        self.assertIn("unset HP_VPS_REPO_TOKEN REPO_AUTH_HEADER", self.text)
        self.assertNotIn("raw.githubusercontent.com/1-vps/hostpanel", self.text)
        self.assertIn("bootstrap-install.sh \\", self.text)
        self.assertIn("tools/validate-production-vm.sh \\", self.text)

    def test_generated_credentials_stay_on_the_vps(self) -> None:
        self.assertNotIn("/var/log/hostpanel-install.log", self.text)
        self.assertIn("PRIVATE_LOG=/root/hostpanel-acceptance-private-install.log", self.text)
        self.assertIn('>> "$PRIVATE_LOG" 2>&1', self.text)
        self.assertEqual(self.text.count("hostpanel-acceptance-private-install.log"), 1)
        self.assertNotIn("install-and-pre-reboot.txt", self.text)
        self.assertNotIn("exec > >(tee", self.text)
        self.assertIn("hostpanel-acceptance-evidence", self.text)


if __name__ == "__main__":
    unittest.main()
