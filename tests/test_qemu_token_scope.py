import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "qemu-vm-acceptance.yml"
HARNESS = ROOT / "tools" / "run-qemu-vm-acceptance.sh"
GUEST_INSTALLER = ROOT / "tools" / "qemu-guest-install.sh"


class QemuTokenScopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")
        cls.harness = HARNESS.read_text(encoding="utf-8")
        cls.guest_installer = GUEST_INSTALLER.read_text(encoding="utf-8")

    def test_repository_token_is_scoped_to_the_install_step(self):
        token_lines = [
            line for line in self.workflow.splitlines()
            if "HP_QEMU_REPO_TOKEN:" in line
        ]
        self.assertEqual(token_lines, ["          HP_QEMU_REPO_TOKEN: ${{ github.token }}"])
        boot_step = self.workflow.index("      - name: Boot, install, reboot, and validate")
        token_reference = self.workflow.index("          HP_QEMU_REPO_TOKEN: ${{ github.token }}")
        harness_call = self.workflow.index("          bash tools/run-qemu-vm-acceptance.sh")
        self.assertLess(boot_step, token_reference)
        self.assertLess(token_reference, harness_call)

    def test_runner_drops_plain_and_encoded_token_before_qemu_starts(self):
        unset = self.harness.index("unset HP_QEMU_REPO_TOKEN REPO_TOKEN REPO_AUTH_HEADER")
        qemu_start = self.harness.index("qemu-system-x86_64")
        self.assertLess(unset, qemu_start)
        self.assertIn('chmod 600 "$WORK_DIR/guest.env"', self.harness)
        self.assertNotIn('$ARTIFACT_DIR/guest.env', self.harness)

    def test_guest_removes_transient_authentication_state(self):
        self.assertIn("rm -f /tmp/guest.env", self.guest_installer)
        self.assertIn("sed -i '/^export GIT_/d' /root/hostpanel-qemu.env", self.guest_installer)
        self.assertIn(
            "unset GIT_CONFIG_COUNT GIT_CONFIG_KEY_0 GIT_CONFIG_VALUE_0 GIT_TERMINAL_PROMPT",
            self.guest_installer,
        )


if __name__ == "__main__":
    unittest.main()
