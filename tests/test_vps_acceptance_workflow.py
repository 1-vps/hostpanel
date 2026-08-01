#!/usr/bin/env python3
import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "vps-acceptance.yml"
REVIEWED_COMMIT = "9c38d0095563ea33efd14124babfd29556c0da46"


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

    def test_checkout_and_install_inputs_are_commit_pinned(self) -> None:
        self.assertIn(f"REVIEWED_COMMIT_SHA: {REVIEWED_COMMIT}", self.text)
        self.assertIn(f"ref: {REVIEWED_COMMIT}", self.text)
        self.assertGreaterEqual(
            self.text.count('[[ "$(git rev-parse HEAD)" == "$REVIEWED_COMMIT_SHA" ]]'),
            2,
        )
        self.assertIn("bootstrap-install.sh", self.text)
        self.assertIn("tools/validate-production-vm.sh", self.text)
        self.assertNotIn("raw.githubusercontent.com/1-vps/hostpanel", self.text)

    def test_install_is_pinned_and_preserves_evidence(self) -> None:
        self.assertIn("EXPECTED_VERSION: 3.4.0", self.text)
        self.assertNotIn("EXPECTED_VERSION: 3.4.0-hardened-r6", self.text)
        self.assertIn("validate-production-vm.sh --prepare-reboot", self.text)
        self.assertIn("validate-production-vm.sh --post-reboot", self.text)
        self.assertIn(
            "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
            self.text,
        )
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

    def test_runner_removes_transient_token_files_on_every_exit(self) -> None:
        self.assertIn("local_cleanup(){", self.text)
        self.assertIn("trap local_cleanup EXIT", self.text)
        self.assertIn(
            "rm -f /tmp/hostpanel-acceptance.env /tmp/hostpanel-acceptance-remote.sh",
            self.text,
        )

    def test_runner_removes_token_file_immediately_after_transfer(self) -> None:
        lines = self.text.splitlines()
        create = lines.index("          } > /tmp/hostpanel-acceptance.env")
        scp_target = lines.index('            root@"$VPS_HOST":/root/')
        cleanup = lines.index("          rm -f /tmp/hostpanel-acceptance.env", scp_target)
        ssh = lines.index('          "${SSH[@]}" root@"$VPS_HOST" \'', cleanup)
        self.assertLess(create, scp_target)
        self.assertEqual(cleanup, scp_target + 1)
        self.assertEqual(ssh, cleanup + 1)
        self.assertGreaterEqual(
            self.text.count("rm -f /tmp/hostpanel-acceptance.env"),
            2,
        )

    def test_remote_acceptance_state_is_removed_after_validation_or_failure(self) -> None:
        post_reboot = self.text.index("      - name: Run post-reboot validation")
        cleanup_start = self.text.index("cleanup_acceptance_state(){", post_reboot)
        trap_install = self.text.index(
            "trap cleanup_acceptance_state EXIT",
            cleanup_start,
        )
        cleanup_function = self.text[cleanup_start:trap_install]
        source_env = self.text.index(
            "source /root/hostpanel-acceptance.env",
            trap_install,
        )
        self.assertLess(cleanup_start, trap_install)
        self.assertLess(trap_install, source_env)
        self.assertIn("local status=$? cleanup_status=0", cleanup_function)
        self.assertIn(
            "if ((status == 0 && cleanup_status == 0)); then",
            cleanup_function,
        )
        self.assertNotIn("|| true", cleanup_function)
        for path in (
            "/root/hostpanel-acceptance.env",
            "/root/bootstrap-install.sh",
            "/root/validate-production-vm.sh",
            "/root/hostpanel-acceptance-remote.sh",
            "/root/hostpanel-acceptance-private-install.log",
        ):
            self.assertIn(path, cleanup_function)

        for original_status, rm_status, expected_status in (
            (0, 0, 0),
            (0, 1, 1),
            (9, 1, 9),
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
            self.assertEqual(result.returncode, expected_status, result.stderr)

        with tempfile.TemporaryDirectory() as temporary_directory:
            trace = pathlib.Path(temporary_directory) / "rm-calls.txt"
            shell = (
                "TRACE_FILE=$1\n"
                "RM_CALLS=0\n"
                "rm() {\n"
                "  RM_CALLS=$((RM_CALLS + 1))\n"
                "  printf '%s\\n' \"$*\" >> \"$TRACE_FILE\"\n"
                "  ((RM_CALLS == 1)) && return 1\n"
                "  return 0\n"
                "}\n"
                f"{cleanup_function}\n"
                "true\n"
                "cleanup_acceptance_state\n"
            )
            result = subprocess.run(
                ["bash", "-c", shell, "bash", str(trace)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 1, result.stderr)
            calls = trace.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(calls), 1)
            self.assertNotIn(
                "/root/hostpanel-acceptance-private-install.log",
                calls[0],
            )

        collect = self.text.index("      - name: Collect root-only validation evidence")
        evidence_copy = self.text.index(
            'root@"$VPS_HOST":/root/hostpanel-acceptance-evidence/.',
            collect,
        )
        fallback_start = self.text.index(
            "timeout 90s sshpass -e ssh",
            evidence_copy,
        )
        fallback_end = self.text.index("          find evidence", fallback_start)
        fallback_cleanup = self.text[fallback_start:fallback_end]
        self.assertLess(evidence_copy, fallback_start)
        self.assertIn("-o ConnectTimeout=20", fallback_cleanup)
        self.assertIn("-o ServerAliveInterval=15", fallback_cleanup)
        self.assertIn("-o ServerAliveCountMax=4", fallback_cleanup)
        for path in (
            "/root/hostpanel-acceptance.env",
            "/root/bootstrap-install.sh",
            "/root/validate-production-vm.sh",
            "/root/hostpanel-acceptance-remote.sh",
        ):
            self.assertIn(path, fallback_cleanup)
        self.assertNotIn(
            "/root/hostpanel-acceptance-private-install.log",
            fallback_cleanup,
        )
        self.assertIn("|| true", fallback_cleanup)

    def test_generated_credentials_stay_on_the_vps(self) -> None:
        self.assertNotIn("/var/log/hostpanel-install.log", self.text)
        self.assertIn("PRIVATE_LOG=/root/hostpanel-acceptance-private-install.log", self.text)
        self.assertIn('>> "$PRIVATE_LOG" 2>&1', self.text)
        self.assertGreaterEqual(
            self.text.count("hostpanel-acceptance-private-install.log"),
            2,
        )
        self.assertNotIn("evidence/hostpanel-acceptance-private-install.log", self.text)
        self.assertNotIn("install-and-pre-reboot.txt", self.text)
        self.assertNotIn("exec > >(tee", self.text)
        self.assertIn("hostpanel-acceptance-evidence", self.text)


if __name__ == "__main__":
    unittest.main()
