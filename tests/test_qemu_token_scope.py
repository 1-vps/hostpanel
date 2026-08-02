import pathlib
import subprocess
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
        token_line = "          HP_QEMU_REPO_TOKEN: ${{ github.token }}"
        token_lines = [
            line for line in self.workflow.splitlines()
            if "HP_QEMU_REPO_TOKEN:" in line
        ]
        self.assertEqual(token_lines, [token_line])
        boot_step = self.workflow.index(
            "  - name: Boot, install candidate, reboot, and validate"
        )
        token_reference = self.workflow.index(token_line)
        harness_call = self.workflow.index(
            "      bash tools/run-qemu-source-candidate.sh"
        )
        self.assertLess(boot_step, token_reference)
        self.assertLess(token_reference, harness_call)

    def test_both_jobs_checkout_and_verify_the_reviewed_head_commit(self):
        checkout_action = (
            "actions/checkout@11d5960a326750d5838078e36cf38b85af677262"
        )
        release_start = self.workflow.index("  release-state-inspection:")
        qemu_start = self.workflow.index("\n  qemu-vm:", release_start)
        release_section = self.workflow[release_start:qemu_start]
        qemu_section = self.workflow[qemu_start:]

        self.assertEqual(release_section.count(checkout_action), 1)
        self.assertIn(
            "REVIEWED_COMMIT: ${{ github.event.pull_request.head.sha || github.sha }}",
            release_section,
        )
        self.assertIn("          ref: ${{ env.REVIEWED_COMMIT }}", release_section)
        self.assertIn('[[ "$REVIEWED_COMMIT" =~ ^[0-9a-f]{40}$ ]]', release_section)
        release_identity = release_section.index(
            'ACTUAL_SHA="$(git rev-parse HEAD)"'
        )
        release_mismatch = release_section.index(
            "QEMU release-state checkout mismatch:",
            release_identity,
        )
        release_execution = release_section.index(
            "python3 tools/inspect-release-state.py",
            release_mismatch,
        )
        self.assertLess(release_identity, release_mismatch)
        self.assertLess(release_mismatch, release_execution)
        self.assertIn('"$REVIEWED_COMMIT" "$ACTUAL_SHA" >&2', release_section)

        self.assertEqual(qemu_section.count(checkout_action), 1)
        self.assertIn(
            "HP_QEMU_REVIEWED_COMMIT_SHA: ${{ github.event.pull_request.head.sha || github.sha }}",
            qemu_section,
        )
        self.assertIn(
            "          ref: ${{ env.HP_QEMU_REVIEWED_COMMIT_SHA }}",
            qemu_section,
        )
        qemu_identity = qemu_section.index('ACTUAL_SHA="$(git rev-parse HEAD)"')
        qemu_mismatch = qemu_section.index(
            "QEMU reviewed checkout mismatch:",
            qemu_identity,
        )
        first_harness_command = qemu_section.index(
            "bash -n tools/run-qemu-vm-acceptance.sh",
            qemu_mismatch,
        )
        self.assertLess(qemu_identity, qemu_mismatch)
        self.assertLess(qemu_mismatch, first_harness_command)
        self.assertIn(
            '"$HP_QEMU_REVIEWED_COMMIT_SHA" "$ACTUAL_SHA" >&2',
            qemu_section,
        )
        self.assertIn("github.event.pull_request.head.sha", self.workflow)

    def test_runner_keeps_token_out_of_qemu_and_limits_auth_file_lifetime(self):
        unset_exported = self.harness.index("unset HP_QEMU_REPO_TOKEN")
        deexport_local = self.harness.index("export -n REPO_TOKEN")
        qemu_start = self.harness.index(
            "env -u HP_QEMU_REPO_TOKEN -u REPO_TOKEN qemu-system-x86_64"
        )
        env_create = self.harness.index('} > "$WORK_DIR/guest.env"')
        scp = self.harness.index('scp "${scp_opts[@]}"', env_create)
        remove_after_scp = self.harness.index(
            'rm -f -- "$WORK_DIR/guest.env"', scp
        )
        guest_start = self.harness.index(
            'ssh "${ssh_opts[@]}" hostpanel@127.0.0.1', remove_after_scp
        )
        self.assertLess(unset_exported, qemu_start)
        self.assertLess(deexport_local, qemu_start)
        self.assertLess(qemu_start, env_create)
        self.assertLess(env_create, scp)
        self.assertLess(scp, remove_after_scp)
        self.assertLess(remove_after_scp, guest_start)
        self.assertEqual(
            self.harness.count('rm -f -- "$WORK_DIR/guest.env"'),
            2,
        )
        cleanup = self.harness.index("collect_evidence(){")
        cleanup_remove = self.harness.index(
            'rm -f -- "$WORK_DIR/guest.env"', cleanup
        )
        evidence_ssh = self.harness.index(
            'ssh "${ssh_opts[@]}" hostpanel@127.0.0.1', cleanup
        )
        self.assertLess(cleanup_remove, evidence_ssh)
        self.assertNotIn('$ARTIFACT_DIR/guest.env', self.harness)

    def test_guest_inputs_are_promoted_from_verified_descriptors(self):
        promotion = self.harness.index('sudo python3 - "$(id -u)" <<PYROOT')
        open_descriptor = self.harness.index("os.open(path,", promotion)
        no_follow = self.harness.index("os.O_NOFOLLOW", open_descriptor)
        descriptor_check = self.harness.index("metadata = os.fstat(source_fd)", no_follow)
        ownership_check = self.harness.index(
            "metadata.st_uid != expected_uid or metadata.st_nlink != 1",
            descriptor_check,
        )
        size_limit = self.harness.index(
            "metadata.st_size > MAX_INPUT_BYTES",
            ownership_check,
        )
        descriptor_copy = self.harness.index("os.dup(source_fd)", size_limit)
        observed_size = self.harness.index("remaining = metadata.st_size", descriptor_copy)
        bounded_read = self.harness.index(
            "source.read(min(1024 * 1024, remaining))",
            observed_size,
        )
        extra_byte_check = self.harness.index("if source.read(1):", bounded_read)
        atomic_replace = self.harness.index("os.replace(temp_name, path)", extra_byte_check)
        root_check = self.harness.index("promoted.st_uid != 0", atomic_replace)
        guest_start = self.harness.index("sudo /tmp/qemu-guest-install.sh", root_check)
        self.assertLess(promotion, open_descriptor)
        self.assertLess(open_descriptor, descriptor_check)
        self.assertLess(descriptor_check, ownership_check)
        self.assertLess(ownership_check, size_limit)
        self.assertLess(size_limit, descriptor_copy)
        self.assertLess(descriptor_copy, observed_size)
        self.assertLess(observed_size, bounded_read)
        self.assertLess(bounded_read, extra_byte_check)
        self.assertLess(extra_byte_check, atomic_replace)
        self.assertLess(atomic_replace, root_check)
        self.assertLess(root_check, guest_start)
        self.assertNotIn("shutil.copyfileobj(source, target)", self.harness)
        self.assertNotIn("sudo chown root:root /tmp/bootstrap-install.sh", self.harness)
        self.assertNotIn("sudo chmod 700 /tmp/bootstrap-install.sh", self.harness)

    def test_guest_removes_transient_authentication_state(self):
        self.assertIn("rm -f /tmp/guest.env", self.guest_installer)
        self.assertIn("clear_repo_auth(){", self.guest_installer)
        self.assertIn("trap clear_repo_auth EXIT", self.guest_installer)
        self.assertIn("sed -i '/^export GIT_/d' /root/hostpanel-qemu.env", self.guest_installer)
        self.assertIn(
            "unset GIT_CONFIG_COUNT GIT_CONFIG_KEY_0 GIT_CONFIG_VALUE_0 GIT_TERMINAL_PROMPT",
            self.guest_installer,
        )

    def test_guest_removes_transient_inputs_and_post_reboot_state(self):
        input_start = self.guest_installer.index("for input in")
        input_end = self.guest_installer.index("; do", input_start)
        input_block = self.guest_installer[input_start:input_end]
        for path in (
            "/tmp/guest.env",
            "/tmp/bootstrap-install.sh",
            "/tmp/validate-production-vm.sh",
            "/tmp/qemu-guest-install.sh",
        ):
            self.assertIn(path, input_block)

        root_copy = self.guest_installer.index(
            "install -o root -g root -m 700 /tmp/validate-production-vm.sh"
        )
        post_reboot = self.guest_installer.index(
            "cat > /root/hostpanel-qemu-post-reboot.sh <<'POSTREBOOT'",
            root_copy,
        )
        tmp_cleanup_block = self.guest_installer[root_copy:post_reboot]
        for path in (
            "/tmp/bootstrap-install.sh",
            "/tmp/validate-production-vm.sh",
            "/tmp/qemu-guest-install.sh",
        ):
            self.assertIn(path, tmp_cleanup_block)

        cleanup_start = self.guest_installer.index(
            "cleanup_acceptance_state(){",
            post_reboot,
        )
        trap_marker = "\n}\ntrap cleanup_acceptance_state EXIT"
        cleanup_end = self.guest_installer.index(trap_marker, cleanup_start) + 2
        cleanup_function = self.guest_installer[cleanup_start:cleanup_end]
        install_trap = self.guest_installer.index(
            "trap cleanup_acceptance_state EXIT",
            cleanup_end,
        )
        source_env = self.guest_installer.index(
            "source /root/hostpanel-qemu.env",
            install_trap,
        )
        self.assertLess(cleanup_start, install_trap)
        self.assertLess(install_trap, source_env)
        self.assertIn("local status=$? cleanup_status=0", cleanup_function)
        self.assertNotIn("|| true", cleanup_function)
        for path in (
            "/root/hostpanel-qemu.env",
            "/root/bootstrap-install.sh",
            "/root/validate-production-vm.sh",
            "/root/hostpanel-qemu-post-reboot.sh",
            "/root/hostpanel-qemu-private-install.log",
        ):
            self.assertIn(path, cleanup_function)

        for original_status, rm_status, expected_status in (
            (0, 0, 0),
            (0, 1, 1),
            (7, 1, 7),
        ):
            shell = (
                f"rm() {{ return {rm_status}; }}\n"
                f"{cleanup_function}\n"
                f"bash -c 'exit {original_status}'\n"
                "cleanup_acceptance_state\n"
            )
            result = subprocess.run(
                ["bash", "-c", shell],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                result.returncode,
                expected_status,
                result.stderr,
            )

    def test_guest_sanitizes_auth_on_preflight_and_install_failure(self):
        self.assertIn("PREFLIGHT_STATUS=$?", self.guest_installer)
        self.assertIn('collect_failure_evidence "$PREFLIGHT_STATUS"', self.guest_installer)
        self.assertIn("INSTALL_STATUS=$?", self.guest_installer)
        self.assertIn('collect_failure_evidence "$INSTALL_STATUS"', self.guest_installer)
        install_status = self.guest_installer.index("INSTALL_STATUS=$?")
        clear_auth = self.guest_installer.index("clear_repo_auth", install_status)
        disable_exit_trap = self.guest_installer.index("trap - EXIT", clear_auth)
        install_failure = self.guest_installer.index(
            'collect_failure_evidence "$INSTALL_STATUS"', disable_exit_trap
        )
        self.assertLess(install_status, clear_auth)
        self.assertLess(clear_auth, disable_exit_trap)
        self.assertLess(disable_exit_trap, install_failure)


if __name__ == "__main__":
    unittest.main()
