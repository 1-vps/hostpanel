import pathlib
import tarfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "qemu-vm-acceptance.yml"
HARNESS = ROOT / "tools" / "run-qemu-vm-acceptance.sh"
GUEST_INSTALLER = ROOT / "tools" / "qemu-guest-install.sh"
VALIDATOR = ROOT / "tools" / "validate-production-vm.sh"


def signed_source_version() -> str:
    archives = sorted(ROOT.glob("hostpanel-*-source.tar.gz"))
    if len(archives) != 1:
        raise RuntimeError(f"expected exactly one signed source archive, found {len(archives)}")
    with tarfile.open(archives[0], "r:gz") as archive:
        matches = [member for member in archive.getmembers() if member.name.endswith("/VERSION")]
        if len(matches) != 1:
            raise RuntimeError(f"expected exactly one release VERSION, found {len(matches)}")
        handle = archive.extractfile(matches[0])
        if handle is None:
            raise RuntimeError("could not read signed release VERSION")
        version = handle.read().decode("utf-8", errors="strict").strip()
    if not version:
        raise RuntimeError("signed release VERSION is empty")
    return version


class QemuVmAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")
        cls.harness = HARNESS.read_text(encoding="utf-8")
        cls.guest_installer = GUEST_INSTALLER.read_text(encoding="utf-8")
        cls.validator = VALIDATOR.read_text(encoding="utf-8")
        cls.signed_version = signed_source_version()

    def test_workflow_is_secretless_and_least_privilege(self):
        self.assertIn("permissions:\n  contents: read", self.workflow)
        self.assertNotIn("secrets.", self.workflow)
        self.assertNotIn("sshpass", self.workflow)
        self.assertIn("head.repo.full_name == github.repository", self.workflow)
        self.assertIn("persist-credentials: false", self.workflow)
        self.assertIn('setfacl -m "u:$(id -u):rw" /dev/kvm', self.workflow)
        self.assertNotIn("chmod a+rw /dev/kvm", self.workflow)
        self.assertNotIn("chmod a+rw /dev/kvm", self.harness)
        self.assertNotIn("/opt/hostedtoolcache", self.workflow)

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

    def test_expected_version_matches_signed_source_version(self):
        self.assertIn(
            f"HP_QEMU_EXPECTED_VERSION: {self.signed_version}",
            self.workflow,
        )
        self.assertIn(
            'EXPECTED_VERSION="${HP_QEMU_EXPECTED_VERSION:-',
            self.harness,
        )
        self.assertIn(
            f'EXPECTED_VERSION="${{HP_EXPECTED_VERSION:-{self.signed_version}}}"',
            self.validator,
        )
        self.assertNotIn("$REPO_ROOT/VERSION", self.harness)
        self.assertIn("HP_QEMU_EXPECTED_VERSION must be a release version", self.harness)

    def test_qemu_paths_cover_all_runtime_overlay_sources(self):
        for path in (
            "tools/harden_install_impl.py",
            "tools/patch_cli_runtime_env.py",
            "tools/patch_panel_ui.py",
            "app/static/panel-redesign.css",
            "app/static/panel-redesign.js",
            "tests/test_panel_redesign.py",
            "tests/test_post_install_health.py",
        ):
            self.assertIn(f"      - {path}", self.workflow)

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
        self.assertIn("manage_etc_hosts: true", self.harness)

    def test_harness_uses_ephemeral_key_auth_and_no_password(self):
        self.assertIn("ssh-keygen -q -t ed25519", self.harness)
        self.assertIn("ssh_pwauth: false", self.harness)
        self.assertIn("BatchMode=yes", self.harness)
        self.assertNotIn("SSHPASS", self.harness)
        self.assertNotIn("VPS_ROOT_PASSWORD", self.harness)

    def test_qemu_cleanup_revalidates_pid_ownership(self):
        self.assertIn("qemu_pid_is_ours(){", self.harness)
        self.assertIn("/proc/$QEMU_PID/cmdline", self.harness)
        self.assertIn("qemu-system-x86_64", self.harness)
        self.assertIn('$WORK_DIR/disk.qcow2', self.harness)
        self.assertIn("qemu_pid_is_ours && kill -KILL", self.harness)
        self.assertNotIn('kill -0 "$QEMU_PID"', self.harness)

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
        self.assertIn("failure_phase=%s", self.guest_installer)
        self.assertIn("expected_version=%s", self.guest_installer)
        self.assertIn("installed_version=%s", self.guest_installer)
        self.assertIn("FAILURE_PHASE=pre-reboot-validation", self.guest_installer)
        self.assertIn(
            "password|passwd|secret|token|credential|private[ _-]?key",
            self.guest_installer,
        )
        self.assertIn("install-failure-summary.txt", self.guest_installer)

    def test_validator_accepts_doctor_warnings_but_not_failures(self):
        self.assertIn("1) warn 'hostpanel-doctor completed with warnings'", self.validator)
        self.assertIn('*) fail "hostpanel-doctor failed with exit status $rc"', self.validator)
        self.assertNotIn("run_required 'hostpanel-doctor passed'", self.validator)

    def test_validator_checks_live_redis_acl_without_password_arguments(self):
        self.assertIn("ACL GETUSER default", self.validator)
        self.assertIn("ACL GETUSER hostpanel", self.validator)
        self.assertIn('REDISCLI_AUTH="$password"', self.validator)
        self.assertIn("grep -Fxq off", self.validator)
        self.assertIn("grep -Fxq on", self.validator)
        self.assertNotIn('redis-cli -a "$password"', self.validator)
        self.assertNotIn("grep -RhsE", self.validator)

    def test_openlitespeed_failure_evidence_is_scoped_and_non_secret(self):
        self.assertIn(
            "Configuring OpenLiteSpeed as a private per-domain backend",
            self.guest_installer,
        )
        self.assertIn("scoped pre-rollback OpenLiteSpeed errors", self.guest_installer)
        self.assertIn("scoped post-rollback OpenLiteSpeed configuration test", self.guest_installer)
        self.assertIn("/usr/local/lsws/bin/openlitespeed -t", self.guest_installer)
        self.assertIn("scoped OpenLiteSpeed managed directives", self.guest_installer)
        self.assertIn("include|listener|address|secure|useIpInProxyHeader", self.guest_installer)
        self.assertIn("grep -Eai '(openlitespeed|litespeed|lsws|httpd_config", self.guest_installer)
        self.assertNotIn("cat /var/log/hostpanel-install.log", self.guest_installer)
        self.assertNotIn('cat "$PRIVATE_LOG"', self.guest_installer)

    def test_hostpanel_service_failure_evidence_is_scoped_and_redacted(self):
        self.assertIn("Registering the systemd service", self.guest_installer)
        self.assertIn("scoped hostpanel service state", self.guest_installer)
        self.assertIn("ExecMainCode,ExecMainStatus", self.guest_installer)
        self.assertIn("journalctl -u hostpanel.service", self.guest_installer)
        self.assertIn("scoped redacted hostpanel service errors", self.guest_installer)
        self.assertIn("[REDACTED]@", self.guest_installer)
        self.assertNotIn("systemctl cat hostpanel", self.guest_installer)
        self.assertNotIn('cat "$PRIVATE_LOG"', self.guest_installer)

    def test_root_action_failure_evidence_is_scoped_and_non_secret(self):
        self.assertIn("Granting the panel controlled root actions", self.guest_installer)
        self.assertIn("scoped pre-rollback root-action errors", self.guest_installer)
        self.assertIn("visudo|sudoers|chown:|chmod:|find:", self.guest_installer)
        self.assertIn("scoped post-rollback sudoers validation", self.guest_installer)
        self.assertIn("visudo -cf /etc/sudoers.d/hostpanel", self.guest_installer)
        self.assertNotIn("cat /var/log/hostpanel-install.log", self.guest_installer)
        self.assertNotIn('cat "$PRIVATE_LOG"', self.guest_installer)


if __name__ == "__main__":
    unittest.main()
