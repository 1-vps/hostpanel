#!/usr/bin/env python3
import pathlib
import subprocess
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "validate-production-vm.sh"
HELPER = ROOT / "tools" / "secure-validation-io.py"


class ProductionVmValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = SCRIPT.read_text(encoding="utf-8")
        cls.helper = HELPER.read_text(encoding="utf-8")

    def test_bash_syntax(self):
        result = subprocess.run(
            ["bash", "-n", str(SCRIPT)],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_help_is_available_without_root(self):
        result = subprocess.run(
            ["bash", str(SCRIPT), "--help"],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("--prepare-reboot", result.stdout)
        self.assertIn("--post-reboot", result.stdout)
        self.assertIn("HP_VALIDATION_IO_HELPER", result.stdout)

    def test_reboot_state_uses_descriptor_bound_atomic_io(self):
        self.assertIn("/proc/sys/kernel/random/boot_id", self.text)
        self.assertIn("pre-reboot.boot-id", self.text)
        self.assertIn('read-state "$REBOOT_STATE"', self.text)
        self.assertIn('write-state "$REBOOT_STATE"', self.text)
        self.assertNotIn('>"$REBOOT_STATE"', self.text)
        self.assertIn("os.O_NOFOLLOW", self.helper)
        self.assertIn("os.O_EXCL", self.helper)
        self.assertIn("os.replace(", self.helper)
        self.assertIn("os.fsync(parent.fd)", self.helper)
        self.assertIn("boot ID changed", self.text)

    def test_report_is_exclusive_descriptor_bound_and_fsynced(self):
        self.assertIn("run-with-report", self.text)
        self.assertIn("HP_VALIDATION_REPORT_ACTIVE", self.text)
        self.assertIn("HP_VALIDATION_REPORT_PATH", self.text)
        self.assertNotIn(': >"$REPORT"', self.text)
        self.assertNotIn("tee -a \"$REPORT\"", self.text)
        self.assertIn("os.O_EXCL", self.helper)
        self.assertIn("os.fsync(report_fd)", self.helper)
        self.assertIn("validation report path changed", self.helper)

    def test_destructive_hooks_execute_the_opened_descriptor(self):
        self.assertIn("HP_DESTRUCTIVE_TESTS", self.text)
        self.assertIn("HP_PROVIDER_INSTANCE_ID", self.text)
        self.assertIn("HP_PROVIDER_SNAPSHOT_ID", self.text)
        self.assertIn("HP_PROVIDER_SNAPSHOT_CREATED_AT_UTC", self.text)
        self.assertNotIn("HP_PROVIDER_SNAPSHOT_CONFIRMED", self.text)
        self.assertIn("HP_BACKUP_TEST_SCRIPT", self.text)
        self.assertIn("HP_RESTORE_TEST_SCRIPT", self.text)
        self.assertIn("HP_FAILURE_INJECTION_SCRIPT", self.text)
        self.assertIn('exec-hook "$path"', self.text)
        self.assertNotIn("validate_hook(){", self.text)
        self.assertIn("os.execve(high_fd", self.helper)
        self.assertIn("os.F_DUPFD_CLOEXEC".replace("os.", "fcntl."), self.helper)
        self.assertIn("must have exactly one hard link", self.helper)
        self.assertIn("group/world writable", self.helper)
        self.assertNotIn("eval ", self.text)

    def test_helper_is_checked_before_privileged_use(self):
        self.assertIn("secure-validation-io.py", self.text)
        self.assertIn("root-owned, single-linked", self.text)
        self.assertIn("helper_mode_value", self.text)
        self.assertIn("-I \"$VALIDATION_IO\"", self.text)

    def test_required_production_checks_are_present(self):
        for marker in (
            "systemctl --failed",
            "hostpanel-doctor",
            "nginx -t",
            "firewall-cmd --state",
            "ufw status",
            "Redis default ACL user is disabled",
            "SMTP port 25",
            "certificate is parseable",
        ):
            self.assertIn(marker, self.text)


if __name__ == "__main__":
    unittest.main()
