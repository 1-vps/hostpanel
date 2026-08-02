#!/usr/bin/env python3
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "vps-acceptance.yml"


class VPSAcceptanceWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def section(self, start: str, end: str) -> str:
        begin = self.text.index(start)
        finish = self.text.index(end, begin)
        return self.text[begin:finish]

    def test_is_manual_main_only_and_environment_gated(self) -> None:
        self.assertIn("workflow_dispatch:", self.text)
        self.assertNotIn("pull_request:", self.text)
        self.assertNotIn("push:\n", self.text)
        self.assertIn("environment: vps-acceptance", self.text)
        self.assertIn("ERASE-AND-INSTALL", self.text)
        self.assertIn('[[ "${{ github.ref }}" == refs/heads/main ]]', self.text)
        self.assertNotIn("VPS_PROVIDER_SNAPSHOT_CONFIRMED", self.text)

    def test_reviewed_commit_is_required_and_checkout_pinned_to_it(self) -> None:
        self.assertIn("reviewed_commit_sha:", self.text)
        self.assertIn("required: true", self.section("reviewed_commit_sha:", "provider_instance_id:"))
        self.assertIn(
            "REVIEWED_COMMIT_SHA: ${{ inputs.reviewed_commit_sha }}",
            self.text,
        )
        self.assertIn("ref: ${{ inputs.reviewed_commit_sha }}", self.text)
        self.assertIn(
            '[[ "$REVIEWED_COMMIT_SHA" =~ ^[0-9a-f]{40}$ ]]',
            self.text,
        )
        self.assertGreaterEqual(
            self.text.count('[[ "$(git rev-parse HEAD)" == "$REVIEWED_COMMIT_SHA" ]]'),
            1,
        )
        self.assertIn('CHECKED_OUT_SHA="$(git rev-parse HEAD)"', self.text)
        self.assertNotIn("REVIEWED_COMMIT_SHA: 9c38d009", self.text)

    def test_current_main_is_checked_before_provider_contact_and_install(self) -> None:
        lookup = 'gh api "repos/$GITHUB_REPOSITORY/git/ref/heads/main" --jq .object.sha'
        self.assertEqual(self.text.count(lookup), 2)
        first_lookup = self.text.index(lookup)
        inventory = self.text.index("      - name: Inventory the disposable VM")
        self.assertLess(first_lookup, inventory)
        install = self.text.index("      - name: Install HostPanel and prepare reboot validation")
        second_lookup = self.text.index(lookup, first_lookup + 1)
        repo_auth = self.text.index("          REPO_AUTH_HEADER=", install)
        self.assertGreater(second_lookup, install)
        self.assertLess(second_lookup, repo_auth)
        self.assertIn("Refusing stale VPS acceptance", self.text)
        self.assertIn("Refusing stale VPS installation", self.text)
        self.assertIn('[[ "$GITHUB_SHA" == "$CURRENT_MAIN_SHA" ]]', self.text)
        self.assertGreaterEqual(self.text.count("GH_TOKEN: ${{ github.token }}"), 2)
        self.assertIn("permissions:\n  contents: read", self.text)
        self.assertNotIn("contents: write", self.text)

    def test_snapshot_gate_is_explicit_fresh_and_instance_bound(self) -> None:
        for input_name in (
            "provider_instance_id:",
            "snapshot_id:",
            "snapshot_created_at_utc:",
            "snapshot_confirmation:",
        ):
            self.assertIn(input_name, self.text)
        self.assertIn("VPS_PROVIDER_INSTANCE_ID: ${{ secrets.VPS_PROVIDER_INSTANCE_ID }}", self.text)
        self.assertIn(
            '[[ "$PROVIDER_INSTANCE_ID" == "$VPS_PROVIDER_INSTANCE_ID" ]]',
            self.text,
        )
        self.assertIn("%Y-%m-%dT%H:%M:%SZ", self.text)
        self.assertIn("dt.timedelta(hours=24)", self.text)
        self.assertIn("dt.timedelta(minutes=5)", self.text)
        self.assertIn(
            "SNAPSHOT-VERIFIED:${PROVIDER_INSTANCE_ID}:${SNAPSHOT_ID}:${REVIEWED_COMMIT_SHA}:${SNAPSHOT_CREATED_AT_UTC}",
            self.text,
        )
        self.assertNotIn("SNAPSHOT_CONFIRMED=yes", self.text)

    def test_exact_commit_and_snapshot_are_recorded_in_evidence(self) -> None:
        evidence_setup = self.section(
            "      - name: Configure strict SSH host verification and acceptance context",
            "      - name: Inventory the disposable VM",
        )
        self.assertIn("$EVIDENCE_ROOT/acceptance-context.txt", evidence_setup)
        for field in (
            "reviewed_commit_sha=%s",
            "current_main_sha=%s",
            "workflow_dispatch_sha=%s",
            "workflow_run_id=%s",
            "workflow_run_attempt=%s",
            "provider_instance_id=%s",
            "snapshot_id=%s",
            "snapshot_created_at_utc=%s",
        ):
            self.assertIn(field, evidence_setup)
        self.assertIn("chmod 600", evidence_setup)

    def test_incomplete_manual_release_checks_are_explicit(self) -> None:
        for requirement in (
            "authoritative_dns_delegation=NOT_AUTOMATED",
            "inbound_outbound_mail_and_authentication=NOT_AUTOMATED",
            "disposable_web_php_database_flow=NOT_AUTOMATED",
            "backup_restore_round_trip=NOT_AUTOMATED",
            "customer_filesystem_quota_enforcement=NOT_AUTOMATED",
            "provider_snapshot_rollback=NOT_AUTOMATED",
            "final_release_readiness=INCOMPLETE_UNTIL_MANUAL_EVIDENCE_ATTACHED",
        ):
            self.assertIn(requirement, self.text)

    def test_job_environment_contains_no_secrets(self) -> None:
        job_env = self.section("    env:\n", "\n\n    steps:")
        self.assertNotIn("${{ secrets.", job_env)
        self.assertNotIn("VPS_ROOT_PASSWORD", job_env)
        self.assertNotIn("VPS_SSH_KNOWN_HOSTS", job_env)
        self.assertGreaterEqual(
            self.text.count("SSHPASS: ${{ secrets.VPS_ROOT_PASSWORD }}"),
            4,
        )
        self.assertEqual(
            self.text.count(
                "VPS_SSH_KNOWN_HOSTS: ${{ secrets.VPS_SSH_KNOWN_HOSTS }}"
            ),
            1,
        )

    def test_ssh_is_fail_closed(self) -> None:
        self.assertIn("StrictHostKeyChecking=yes", self.text)
        self.assertIn("UserKnownHostsFile=", self.text)
        self.assertIn("PreferredAuthentications=password", self.text)
        self.assertNotIn("StrictHostKeyChecking=no", self.text)
        self.assertNotIn("UserKnownHostsFile=/dev/null", self.text)

    def test_private_repository_auth_is_transient(self) -> None:
        token_lines = [
            line
            for line in self.text.splitlines()
            if "HP_VPS_REPO_TOKEN:" in line
        ]
        self.assertEqual(
            token_lines,
            ["          HP_VPS_REPO_TOKEN: ${{ github.token }}"],
        )
        self.assertIn("REPO_AUTH_HEADER=", self.text)
        self.assertIn("export GIT_CONFIG_COUNT=1", self.text)
        self.assertIn("clear_repo_auth(){", self.text)
        self.assertIn("sed -i '/^export GIT_/d'", self.text)
        self.assertIn(
            "unset GIT_CONFIG_COUNT GIT_CONFIG_KEY_0 GIT_CONFIG_VALUE_0 GIT_TERMINAL_PROMPT",
            self.text,
        )
        self.assertIn("unset HP_VPS_REPO_TOKEN REPO_AUTH_HEADER", self.text)

    def test_runner_and_remote_cleanup_cover_transient_state(self) -> None:
        self.assertIn("local_cleanup(){", self.text)
        self.assertIn("trap local_cleanup EXIT", self.text)
        self.assertGreaterEqual(
            self.text.count("rm -f /tmp/hostpanel-acceptance.env"),
            2,
        )
        for path in (
            "/root/hostpanel-acceptance.env",
            "/root/bootstrap-install.sh",
            "/root/validate-production-vm.sh",
            "/root/hostpanel-acceptance-remote.sh",
        ):
            self.assertIn(path, self.text)

    def test_install_inputs_come_from_the_verified_checkout(self) -> None:
        self.assertIn("bootstrap-install.sh", self.text)
        self.assertIn("tools/validate-production-vm.sh", self.text)
        self.assertNotIn("raw.githubusercontent.com/1-vps/hostpanel", self.text)
        self.assertIn("persist-credentials: false", self.text)

    def test_external_network_acceptance_fails_closed(self) -> None:
        probes = self.section(
            "      - name: Run external network probes",
            "      - name: Collect root-only validation evidence",
        )
        self.assertNotIn("|| true", probes)
        self.assertIn('grep -Fxq "$VPS_HOST"', probes)
        self.assertIn('curl -fsSI', probes)
        self.assertNotIn('curl -k', probes)
        self.assertIn('nc -z -w 8 "$VPS_HOST" "$port"', probes)
        self.assertNotIn("CLOSED_OR_FILTERED", probes)

    def test_remote_evidence_collection_and_cleanup_fail_closed(self) -> None:
        collection = self.section(
            "      - name: Collect root-only validation evidence",
            "      - name: Sanitize and seal provider evidence before upload",
        )
        self.assertIn("collection_failed=false", collection)
        self.assertIn("cleanup_failed=false", collection)
        self.assertIn("remote_evidence_collection=%s", collection)
        self.assertIn("remote_transient_cleanup=%s", collection)
        self.assertIn("Remote provider evidence collection failed", collection)
        self.assertIn("Remote transient-state cleanup failed", collection)
        self.assertNotIn("evidence/. || true", collection)
        self.assertNotIn("hostpanel-acceptance-remote.sh' \\\n            || true", collection)

    def test_provider_evidence_is_sanitized_sealed_and_only_then_uploaded(self) -> None:
        collect = self.text.index("      - name: Collect root-only validation evidence")
        seal = self.text.index("      - name: Sanitize and seal provider evidence before upload")
        upload = self.text.index("      - name: Upload sealed acceptance evidence")
        self.assertLess(collect, seal)
        self.assertLess(seal, upload)
        self.assertIn("tools/prepare-provider-evidence.py", self.text)
        self.assertIn("tools/sanitize-provider-evidence.py", self.text)
        self.assertIn("tools/seal-provider-evidence.py", self.text)
        self.assertIn("steps.provider_evidence.outputs.ready == 'true'", self.text)
        self.assertIn("provider-artifacts/hostpanel-vps-acceptance.tar", self.text)
        self.assertIn("provider-artifacts/hostpanel-vps-acceptance.tar.sha256", self.text)
        upload_section = self.text[upload:]
        self.assertNotIn("path: evidence", upload_section)
        self.assertNotIn("path: provider-artifacts/evidence", upload_section)

    def test_generated_credentials_stay_on_the_vps(self) -> None:
        self.assertNotIn("/var/log/hostpanel-install.log", self.text)
        self.assertIn(
            "PRIVATE_LOG=/root/hostpanel-acceptance-private-install.log",
            self.text,
        )
        self.assertIn('>> "$PRIVATE_LOG" 2>&1', self.text)
        self.assertNotIn("hostpanel-acceptance-private-install.log", self.section(
            "      - name: Sanitize and seal provider evidence before upload",
            "      - name: Upload sealed acceptance evidence",
        ))


if __name__ == "__main__":
    unittest.main()
