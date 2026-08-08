#!/usr/bin/env python3
import os
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

    def test_installer_log_is_validated_before_root_writes(self):
        self.assertIn(
            'LOG="/var/log/hostpanel-installer/install.log"',
            self.installer,
        )
        self.assertIn('prepare_root_log "$LOG"', self.installer)
        self.assertNotIn('touch "$LOG"; chmod 600 "$LOG"', self.installer)
        helper_start = self.installer.index("prepare_root_log(){")
        helper_end = self.installer.index("\n}\n", helper_start) + 3
        helper = self.installer[helper_start:helper_end]
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            victim = root / "victim"
            victim.write_text("do not change", encoding="utf-8")
            victim.chmod(0o644)
            log = root / "install.log"
            log.symlink_to(victim)
            script = root / "test-log.sh"
            script.write_text(
                "#!/usr/bin/env bash\nset -euo pipefail\n"
                "die(){ printf '%s\\n' \"$*\" >&2; exit 1; }\n"
                f"{helper}\nprepare_root_log \"$1\"\n",
                encoding="utf-8",
            )
            unsafe_parent = root / "unsafe"
            unsafe_parent.mkdir(mode=0o777)
            unsafe_parent.chmod(0o777)
            unsafe_log = unsafe_parent / "install.log"
            system_log_parent = root / "var-log"
            system_log_parent.mkdir(mode=0o775)
            system_log_parent.chmod(0o775)
            secure_parent = system_log_parent / "hostpanel-installer"
            secure_log = secure_parent / "install.log"

            command_prefix = []
            restore_owner = False
            if os.geteuid() != 0:
                sudo = subprocess.run(
                    ["sudo", "-n", "true"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
                if sudo.returncode != 0:
                    self.skipTest("root privileges are required for the root-log runtime test")
                subprocess.run(
                    [
                        "sudo", "-n", "chown", "root:root",
                        str(root), str(system_log_parent),
                    ],
                    check=True,
                )
                command_prefix = ["sudo", "-n"]
                restore_owner = True

            try:
                symlink_result = subprocess.run(
                    command_prefix + ["bash", str(script), str(log)],
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    check=False,
                )
                unsafe_parent_result = subprocess.run(
                    command_prefix + ["bash", str(script), str(unsafe_log)],
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    check=False,
                )
                secure_parent_result = subprocess.run(
                    command_prefix + ["bash", str(script), str(secure_log)],
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    check=False,
                )
                secure_parent_stat = subprocess.run(
                    command_prefix + ["stat", "-c", "%u:%a", str(secure_parent)],
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    check=False,
                )
                secure_log_stat = subprocess.run(
                    command_prefix + ["stat", "-c", "%u:%a", str(secure_log)],
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    check=False,
                )
            finally:
                if restore_owner:
                    subprocess.run(
                        [
                            "sudo", "-n", "chown", "-R",
                            f"{os.getuid()}:{os.getgid()}", str(root),
                        ],
                        check=True,
                    )

            self.assertNotEqual(
                symlink_result.returncode, 0, symlink_result.stdout,
            )
            self.assertIn("regular non-symlink file", symlink_result.stdout)
            self.assertEqual(victim.read_text(encoding="utf-8"), "do not change")
            self.assertEqual(victim.stat().st_mode & 0o777, 0o644)
            self.assertNotEqual(
                unsafe_parent_result.returncode, 0, unsafe_parent_result.stdout,
            )
            self.assertIn(
                "root-owned and not group/world-writable",
                unsafe_parent_result.stdout,
            )
            self.assertFalse(unsafe_log.exists())
            self.assertEqual(secure_parent_result.returncode, 0, secure_parent_result.stdout)
            self.assertEqual(secure_parent_stat.stdout.strip(), "0:700")
            self.assertEqual(secure_log_stat.stdout.strip(), "0:600")

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
        self.assertIn('/etc/hostpanel/php-recommended-missing', self.installer)
        self.assertIn('PHP $version CLI is missing required loaded module', self.installer)
        self.assertIn('PHP $version FPM is missing required loaded module', self.installer)
        self.assertIn("PHP_BRANCHES=(8.5 8.4 8.3 8.2 8.1)", self.installer)
        for module in (
            'curl', 'dom', 'exif', 'fileinfo', 'intl', 'mbstring', 'openssl',
            'pdo_mysql', 'pdo_pgsql', 'pdo_sqlite', 'sodium', 'xmlreader',
            'xmlwriter', 'zip', 'mysqli',
        ):
            self.assertIn(module, self.installer)

    def test_php_recommended_and_full_profiles_are_expanded(self):
        self.assertIn('zstd xsl enchant odbc)', self.installer)
        self.assertIn('uuid event)', self.installer)
        for module in ('apcu', 'imagick', 'redis', 'memcached', 'pcntl', 'sockets', 'xsl', 'yaml'):
            self.assertIn(module, self.installer)

    def test_php_crypto_and_https_runtime_capabilities_fail_closed(self):
        self.assertIn('PASSWORD_BCRYPT', self.installer)
        self.assertIn('["cost" => 12]', self.installer)
        self.assertIn('password_verify($password, $hash)', self.installer)
        self.assertIn('curl_version()', self.installer)
        self.assertIn('in_array("https"', self.installer)
        self.assertIn('sodium_crypto_pwhash', self.installer)
        self.assertIn('random_bytes', self.installer)
        self.assertIn('failed bcrypt, cURL HTTPS, sodium, or CSPRNG runtime validation', self.installer)

    def test_operator_baseline_tools_are_installed_and_validated(self):
        self.assertIn('hostname btop nano plocate)', self.installer)
        self.assertIn("if pkg_available mlocate; then printf 'mlocate'; else printf 'plocate'; fi", self.installer)
        self.assertIn('pkg_map needrestart smartmontools prometheus-node-exporter podman-compose btop', self.installer)
        for utility in ('locate', 'updatedb', 'nano'):
            self.assertIn(f'command -v "$utility"', self.installer)
        self.assertIn('command -v btop', self.installer)
        self.assertIn('role packages installed and operator utilities validated', self.installer)

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
