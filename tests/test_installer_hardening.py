#!/usr/bin/env python3
import pathlib
import re
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def generated_installer() -> str:
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


class InstallerHardeningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.installer = generated_installer()
        cls.launcher = (ROOT / "install.sh").read_text(encoding="utf-8")
        cls.bootstrap = (ROOT / "bootstrap-install.sh").read_text(encoding="utf-8")

    def test_launcher_verifies_the_preserved_base_blob(self):
        self.assertIn('EXPECTED_BASE_BLOB="17424f62d177706a096d1f600e5a702c9ce99498"', self.launcher)
        self.assertIn('git hash-object "$BASE_INSTALLER"', self.launcher)
        self.assertIn('tools/harden_install.py', self.launcher)

    def test_root_only_snapshots_are_separate_from_panel_backups(self):
        self.assertIn('INSTALL_SNAPSHOT_DIR="/var/backups/hostpanel-install"', self.installer)
        self.assertIn('install -d -o root -g root -m 700 "$INSTALL_SNAPSHOT_DIR"', self.installer)
        self.assertIn('mktemp "$INSTALL_SNAPSHOT_DIR/reinstall-', self.installer)
        self.assertNotIn('$BACKUP_DIR/install/reinstall-', self.installer)

    def test_failure_rollback_tracks_packages_and_managed_paths(self):
        self.assertIn('NEW_PACKAGES=()', self.installer)
        self.assertIn('rollback_new_packages', self.installer)
        self.assertIn('remove_paths_absent_before_install', self.installer)
        self.assertIn('/etc/apt/sources.list.d', self.installer)
        self.assertIn('/etc/firewalld', self.installer)
        self.assertIn('/etc/fstab', self.installer)

    def test_firewall_preserves_ssh_and_has_timed_rollback(self):
        self.assertIn('SSH_CONNECTION', self.installer)
        self.assertIn('sshd -T', self.installer)
        self.assertIn('systemd-run --quiet', self.installer)
        self.assertIn('HP_PANEL_ADMIN_CIDR', self.installer)
        self.assertIn('HP_ALLOW_PUBLIC_PANEL', self.installer)

    def test_admin_password_is_not_in_python_arguments(self):
        self.assertIn('password = sys.stdin.read()', self.installer)
        self.assertNotRegex(
            self.installer,
            re.compile(r"store\.init\('admin', '\$ADMIN_PASS'\)"),
        )

    def test_redis_default_user_is_disabled(self):
        self.assertIn('user default off', self.installer)
        self.assertIn('user hostpanel on >$REDIS_PASSWORD', self.installer)
        self.assertIn('username = "hostpanel";', self.installer)
        self.assertIn('HP_REDIS_URL=', self.installer)

    def test_required_services_and_doctor_fail_closed(self):
        for diagnostic in (
            "Dovecot failed to restart",
            "Redis failed to start",
            "PostgreSQL failed to start",
            "Rspamd or Postfix failed to restart",
            "Rspamd or Exim failed to restart",
            "Apache failed to restart",
            "Post-install health check failed",
        ):
            self.assertIn(diagnostic, self.installer)

    def test_mutable_vendor_bootstrap_execution_is_removed(self):
        self.assertNotIn('bash "$LITESPEED_REPO_SCRIPT"', self.installer)
        self.assertNotIn('https://repo.litespeed.sh', self.installer)
        self.assertIn('Automatic external repository setup is disabled', self.installer)

    def test_panel_certificate_uses_configured_host(self):
        self.assertIn('-subj "/CN=$PANEL_HOST"', self.installer)
        self.assertIn('subjectAltName=DNS:$PANEL_HOST', self.installer)

    def test_php_modules_are_checked_after_install(self):
        self.assertIn('/etc/hostpanel/php-skipped-packages', self.installer)
        self.assertIn('PHP $version is missing required loaded module', self.installer)
        self.assertIn("PHP_BRANCHES=(8.5 8.4 8.3 8.2 8.1)", self.installer)

    def test_bootstrap_has_independent_trust_root(self):
        self.assertIn('TRUSTED_RELEASE_PUBLIC_KEY', self.bootstrap)
        self.assertIn('verify_commit_file', self.bootstrap)
        self.assertNotIn('PUBLIC_KEY="$CHECKOUT/', self.bootstrap)
        self.assertNotIn('release-hotfixes/', self.bootstrap)
        for path in (
            'install.sh',
            'install.base.sh',
            'tools/harden_install.py',
            'tools/harden_install_runtime.py',
        ):
            self.assertIn(path, self.bootstrap)


if __name__ == "__main__":
    unittest.main()
