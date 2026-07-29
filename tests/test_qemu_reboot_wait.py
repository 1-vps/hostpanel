import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
GUEST_INSTALLER = ROOT / "tools" / "qemu-guest-install.sh"


class QemuPostRebootWaitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.guest = GUEST_INSTALLER.read_text(encoding="utf-8")

    def test_post_reboot_wait_is_bounded(self):
        self.assertIn("for attempt in $(seq 1 30); do", self.guest)
        self.assertIn("systemctl is-active --quiet hostpanel.service", self.guest)
        self.assertIn("sleep 2", self.guest)
        self.assertIn("hostpanel-post-reboot-failure.txt", self.guest)

    def test_post_reboot_failure_evidence_is_scoped_and_redacted(self):
        self.assertIn("scoped hostpanel service state after reboot", self.guest)
        self.assertIn("journalctl -u hostpanel.service", self.guest)
        self.assertIn("[REDACTED]@", self.guest)
        self.assertNotIn("systemctl cat hostpanel.service", self.guest)

    def test_duplicate_doctor_accepts_warning_exit_status(self):
        self.assertIn("DOCTOR_STATUS=${PIPESTATUS[0]}", self.guest)
        self.assertIn("0|1) ;;", self.guest)
        self.assertIn('*) exit "$DOCTOR_STATUS" ;;', self.guest)


if __name__ == "__main__":
    unittest.main()
