#!/usr/bin/env python3
import pathlib
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "validate-production-vm.sh"


class ProductionVmValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = SCRIPT.read_text(encoding="utf-8")

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

    def test_reboot_is_verified_by_boot_id(self):
        self.assertIn("/proc/sys/kernel/random/boot_id", self.text)
        self.assertIn("pre-reboot.boot-id", self.text)
        self.assertIn("boot ID changed", self.text)

    def test_destructive_hooks_are_fail_closed(self):
        self.assertIn("HP_DESTRUCTIVE_TESTS", self.text)
        self.assertIn("HP_PROVIDER_SNAPSHOT_CONFIRMED", self.text)
        self.assertIn("HP_BACKUP_TEST_SCRIPT", self.text)
        self.assertIn("HP_RESTORE_TEST_SCRIPT", self.text)
        self.assertIn("HP_FAILURE_INJECTION_SCRIPT", self.text)
        self.assertIn("root-owned and not group/world writable", self.text)
        self.assertNotIn("eval ", self.text)

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
