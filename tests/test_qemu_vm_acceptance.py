import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "qemu-vm-acceptance.yml"
HARNESS = ROOT / "tools" / "run-qemu-vm-acceptance.sh"
GUEST_INSTALLER = ROOT / "tools" / "qemu-guest-install.sh"


class QemuVmAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")
        cls.harness = HARNESS.read_text(encoding="utf-8")
        cls.guest_installer = GUEST_INSTALLER.read_text(encoding="utf-8")

    def test_workflow_is_secretless_and_least_privilege(self):
        self.assertIn("permissions:\n  contents: read", self.workflow)
        self.assertNotIn("secrets.", self.workflow)
        self.assertNotIn("sshpass", self.workflow)
        self.assertIn("head.repo.full_name == github.repository", self.workflow)
        self.assertIn("persist-credentials: false", self.workflow)
        self.assertIn('setfacl -m "u:$(id -u):rw" /dev/kvm', self.workflow)
        self.assertNotIn("chmod a+rw /dev/kvm", self.workflow)
        self.assertNotIn("chmod a+rw /dev/kvm", self.harness)

    def test_actions_are_commit_pinned_and_evidence_is_always_uploaded(self):
        self.assertIn(
            "actions/checkout@11d5960a326750d5838078e36cf38b85af677262",
            self.workflow,
        )
        self.assertIn(
            "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
            self.workflow,
        )
        self.assertIn("if: ${{ always() }}", self.workflow)
        self.assertIn("retention-days: 14", self.workflow)

    def test_expected_version_is_explicit_and_validated(self):
        self.assertIn(
            "HP_QEMU_EXPECTED_VERSION: 3.4.0-hardened-r6",
            self.workflow,
        )
        self.assertIn(
            'EXPECTED_VERSION="${HP_QEMU_EXPECTED_VERSION:-3.4.0-hardened-r6}"',
            self.harness,
        )
        self.assertNotIn("$REPO_ROOT/VERSION", self.harness)
        self.assertIn("HP_QEMU_EXPECTED_VERSION must be a release version", self.harness)

    def test_harness_uses_pinned_verified_ubuntu_image(self):
        self.assertIn(
            "release-20260725/ubuntu-24.04-server-cloudimg-amd64.img",
            self.harness,
        )
        self.assertIn(
            "d1940f7d69d343355e183dff1e08a59852d32e7309baa7a4bad8365b11b005ac",
            self.harness,
        )
        self.assertIn("sha256sum -c image.sha256", self.harness)
        self.assertIn("cloud-localds", self.harness)

    def test_harness_uses_ephemeral_key_auth_and_no_password(self):
        self.assertIn("ssh-keygen -q -t ed25519", self.harness)
        self.assertIn("ssh_pwauth: false", self.harness)
        self.assertIn("BatchMode=yes", self.harness)
        self.assertNotIn("SSHPASS", self.harness)
        self.assertNotIn("VPS_ROOT_PASSWORD", self.harness)

    def test_harness_performs_real_systemd_reboot_acceptance(self):
        self.assertIn('test "$(cat /proc/1/comm)" = systemd', self.harness)
        self.assertIn("--prepare-reboot", self.guest_installer)
        self.assertIn("sudo systemctl reboot", self.harness)
        self.assertIn("--post-reboot", self.guest_installer)
        self.assertIn("guest boot ID did not change", self.harness)
        self.assertIn("hostfwd=tcp:127.0.0.1:${SSH_PORT}-:22", self.harness)
        self.assertIn("set -Eeuo pipefail", self.guest_installer)
        self.assertIn("hostpanel-qemu-post-reboot.sh", self.harness)
        self.assertIn("hostpanel-qemu-post-reboot.sh", self.guest_installer)

    def test_guest_inputs_are_escaped_and_checked_before_root_use(self):
        self.assertIn("printf 'HP_PANEL_HOST=%q\\n'", self.harness)
        self.assertIn('test ! -L "$path"', self.harness)
        self.assertIn('test "$(stat -c %h "$path")" = 1', self.harness)
        self.assertIn('[[ -f "$input" && ! -L "$input" ]]', self.guest_installer)
        self.assertIn("0:600:1", self.guest_installer)
        self.assertIn("/root/hostpanel-qemu.env", self.guest_installer)

    def test_guest_evidence_archive_is_validated_before_extraction(self):
        self.assertIn("unsafe guest evidence path", self.harness)
        self.assertIn("member.issym() or member.islnk()", self.harness)
        self.assertIn("max_total_size", self.harness)
        self.assertIn('target.open("xb")', self.harness)
        self.assertNotIn("tar -xzf", self.harness)

    def test_generated_credentials_and_install_output_remain_private(self):
        self.assertIn(
            "generated credentials stay in the root-only guest log",
            self.guest_installer,
        )
        self.assertIn(
            "hostpanel-qemu-private-install.log",
            self.guest_installer,
        )
        self.assertNotIn('$PRIVATE_LOG" > "$EVIDENCE', self.guest_installer)
        self.assertNotIn('cat "$PRIVATE_LOG"', self.guest_installer)

    def test_failure_evidence_is_stage_based_and_redacted(self):
        self.assertIn("/etc/hostpanel/install-state", self.guest_installer)
        self.assertIn("redacted installer errors", self.guest_installer)
        self.assertIn(
            "password|passwd|secret|token|credential|private[ _-]?key",
            self.guest_installer,
        )
        self.assertIn("install-failure-summary.txt", self.guest_installer)


if __name__ == "__main__":
    unittest.main()
