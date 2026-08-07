from __future__ import annotations

import pathlib
import shutil
import stat
import subprocess
import tempfile
import unittest

try:
    import yaml
except ModuleNotFoundError:
    yaml = None

ROOT = pathlib.Path(__file__).resolve().parents[1]
PIPELINE = ROOT / ".buildkite/pipeline.yml"
CONFIGURE = ROOT / ".buildkite/agent/configure-agent.sh"
BOUND = ROOT / ".buildkite/agent/configure-bound-agent.sh"
PRE_BOOTSTRAP = ROOT / ".buildkite/agent/pre-bootstrap"
PRE_COMMAND = ROOT / ".buildkite/agent/pre-command-pipeline-id"
CHECKOUT_POLICY = ROOT / ".buildkite/agent/environment-checkout-policy"
PRIVATE_CHECKOUT = ROOT / ".buildkite/agent/checkout-private-repo"
TRUSTED_UPLOAD = ROOT / ".buildkite/agent/upload-trusted-pipeline.sh"
VERIFY = ROOT / ".buildkite/scripts/verify-build.sh"
RUN_CHECK = ROOT / ".buildkite/scripts/run-check.sh"
RUN_QEMU = ROOT / ".buildkite/scripts/run-qemu.sh"
CONTRACT = ROOT / ".buildkite/scripts/run-pipeline-contract.sh"
SMOKE = ROOT / ".buildkite/operator/smoke-test-agent.sh"
CHECKOUT_KEYS = ROOT / ".buildkite/operator/generate-checkout-key.sh"
POLICY = ROOT / ".buildkite/operator/render-secret-policy.py"
PROVENANCE = ROOT / ".buildkite/operator/verify-provisioning-source.py"
DOCS = ROOT / "BUILDKITE.md"
CODEOWNERS = ROOT / ".github/CODEOWNERS"

PIPELINE_ID = "11111111-1111-4111-8111-111111111111"
QEMU_QUEUE_ID = "33333333-3333-4333-8333-333333333333"


def command_steps(items: list[object]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if "command" in item:
            result.append(item)
        nested = item.get("steps")
        if isinstance(nested, list):
            result.extend(command_steps(nested))
    return result


@unittest.skipIf(yaml is None, "PyYAML is required for Buildkite pipeline contract tests")
class BuildkitePipelineContractTests(unittest.TestCase):
    def test_pipeline_has_no_checkout_secret(self) -> None:
        payload = yaml.safe_load(PIPELINE.read_text(encoding="utf-8"))
        self.assertIsInstance(payload, dict)
        self.assertEqual(payload["env"]["BUILDKITE_CLEAN_CHECKOUT"], "true")
        steps = command_steps(payload["steps"])
        self.assertEqual(len(steps), 16)
        for step in steps:
            self.assertRegex(step["command"], r"^\.buildkite/scripts/[a-z0-9-]+\.sh$")
            self.assertNotIn("checkout", step)
        text = PIPELINE.read_text(encoding="utf-8")
        for forbidden in ("HP_GIT_SSH_KEY", "ssh_secret", "artifact"):
            self.assertNotIn(forbidden, text)

    def test_qemu_step_is_isolated_to_main(self) -> None:
        payload = yaml.safe_load(PIPELINE.read_text(encoding="utf-8"))
        qemu = next(
            step for step in command_steps(payload["steps"])
            if step["key"] == "qemu-vm-acceptance"
        )
        self.assertEqual(qemu["agents"], {"queue": "hostpanel-qemu"})
        self.assertEqual(qemu["if"], 'build.pull_request.id == null && build.branch == "main"')

    def test_one_job_worker_capacity_matches_pipeline_shape(self) -> None:
        payload = yaml.safe_load(PIPELINE.read_text(encoding="utf-8"))
        default_agents = payload["agents"]
        steps = command_steps(payload["steps"])
        ci_steps = [
            step for step in steps
            if step.get("agents", default_agents) == {"queue": "hostpanel-ci"}
        ]
        qemu_steps = [
            step for step in steps
            if step.get("agents", default_agents) == {"queue": "hostpanel-qemu"}
        ]
        self.assertEqual(len(ci_steps), 15)
        self.assertEqual(len(qemu_steps), 1)
        docs = DOCS.read_text(encoding="utf-8")
        for expected in (
            "15 fresh `hostpanel-ci` one-job worker instances per build",
            "one fresh `hostpanel-upload` worker per build",
            "one fresh `hostpanel-qemu` worker for each eligible `main` build",
            "must not enable the pipeline with one static worker per queue",
            "17 fresh worker instances",
        ):
            self.assertIn(expected, docs)

    def test_agent_installs_private_checkout_only_on_runners(self) -> None:
        text = CONFIGURE.read_text(encoding="utf-8")
        for expected in (
            "(3, 134, 0)",
            'checkout-override-mode="strict"',
            "no-command-eval=true",
            "no-plugins=true",
            "no-local-hooks=true",
            "no-git-submodules=true",
            "no-ssh-keyscan=true",
            'verification-failure-behavior="block"',
            "disconnect-after-job=true",
            "checkout-private-repo",
            "/etc/buildkite-agent/hooks/checkout",
            "runner queues require a local --checkout-key",
            "upload queue must not receive --checkout-key",
        ):
            self.assertIn(expected, text)
        self.assertNotIn('git-commit-verification="strict"', text)
        self.assertNotIn("HP_GIT_SSH_KEY", text)

    def test_operator_inputs_are_installed_only_from_fd_validated_stage(self) -> None:
        text = CONFIGURE.read_text(encoding="utf-8")
        for expected in (
            'validated_input_dir="$(mktemp -d /run/hostpanel-buildkite-inputs.XXXXXX)"',
            'chmod 0700 "$validated_input_dir"',
            'def fd_filesystem_type(fd: int) -> str:',
            '/proc/self/fdinfo/{fd}',
            '/proc/self/mountinfo',
            'validated input staging directory must reside on tmpfs',
            'require_tmpfs=True',
            'stage_bytes("agent-token", token_data)',
            'stage_bytes("verification.jwks", verification_data)',
            'stage_bytes("signing.jwks", signing_data)',
            'stage_bytes("checkout-deploy-key", key)',
            'install -o root -g root -m 0600 "$staged_token_file" "$token_destination"',
            'install -o root -g buildkite-agent -m 0640 "$staged_verification_jwks" /etc/buildkite-agent/keys/verification.jwks',
            'install -o root -g buildkite-agent -m 0640 "$staged_signing_jwks" /etc/buildkite-agent/keys/signing.jwks',
            'install -o buildkite-agent -g buildkite-agent -m 0400 "$staged_checkout_key" "$checkout_destination"',
            "O_NOFOLLOW",
            "input changed while read",
            "--checkout-key must reside on tmpfs",
        ):
            self.assertIn(expected, text)
        for forbidden in (
            'install -o root -g root -m 0600 "$token_file" "$token_destination"',
            'install -o root -g buildkite-agent -m 0640 "$verification_jwks" /etc/buildkite-agent/keys/verification.jwks',
            'install -o root -g buildkite-agent -m 0640 "$signing_jwks" /etc/buildkite-agent/keys/signing.jwks',
            'install -o buildkite-agent -g buildkite-agent -m 0400 "$checkout_key" "$checkout_destination"',
            'findmnt -n -o FSTYPE --target "$checkout_key"',
        ):
            self.assertNotIn(forbidden, text)

    def test_static_bootstrap_is_signed_before_blocking_upload_worker_runs(self) -> None:
        docs = DOCS.read_text(encoding="utf-8")
        for expected in (
            "buildkite-agent tool sign",
            "--graphql-token '<buildkite-write-pipelines-token>'",
            "--jwks-file /root/signing-private.jwks",
            "--jwks-key-id hostpanel-2026-08",
            "--organization-slug '<organization-slug>'",
            "--pipeline-slug '<pipeline-slug>'",
            "--update",
            "before any `hostpanel-upload` worker is started",
        ):
            self.assertIn(expected, docs)
        self.assertIn('verification-failure-behavior="block"', CONFIGURE.read_text(encoding="utf-8"))

    def test_checkout_key_is_local_and_destroyed_before_command(self) -> None:
        configure = CONFIGURE.read_text(encoding="utf-8")
        checkout = PRIVATE_CHECKOUT.read_text(encoding="utf-8")
        self.assertIn("/run/hostpanel-buildkite/checkout-deploy-key", configure)
        self.assertIn("-o buildkite-agent -g buildkite-agent -m 0400", configure)
        self.assertIn("--checkout-key must reside on tmpfs", configure)
        self.assertIn("runner workers must have swap disabled", configure)
        self.assertIn("LimitCORE=0", configure)
        self.assertIn('rm -f -- "$checkout_key"', configure)
        self.assertIn("consumed --checkout-key still exists", configure)
        self.assertIn("cleanup_checkout_key", checkout)
        self.assertIn("trap cleanup_checkout_key EXIT", checkout)
        self.assertIn("StrictHostKeyChecking=yes", checkout)
        self.assertIn("IdentitiesOnly=yes", checkout)
        self.assertIn("core.hooksPath=/dev/null", checkout)
        self.assertIn("GIT_CONFIG_COUNT", checkout)
        self.assertIn("/usr/bin/timeout --signal=TERM --kill-after=10s 600", checkout)
        self.assertIn("checkout credential survived checkout", checkout)
        self.assertIn("key_data = read_checked(key, 0o400, os.getuid())", checkout)
        self.assertNotIn("key.read_bytes()", checkout)
        for forbidden in ("secret get", "artifact download", "HP_GIT_SSH_KEY"):
            self.assertNotIn(forbidden, checkout)

    def test_checkout_environment_hook_remains_queue_authority(self) -> None:
        text = CHECKOUT_POLICY.read_text(encoding="utf-8")
        self.assertIn("BUILDKITE_SKIP_CHECKOUT=true", text)
        self.assertIn("BUILDKITE_SKIP_CHECKOUT=false", text)
        self.assertIn("BUILDKITE_CLEAN_CHECKOUT=true", text)
        self.assertIn("O_NOFOLLOW", text)
        self.assertIn("st_uid != 0", text)

    def test_trusted_uploader_only_uploads_pinned_pipeline(self) -> None:
        text = TRUSTED_UPLOAD.read_text(encoding="utf-8")
        for expected in (
            "/etc/buildkite-agent/hostpanel-pipeline.yml",
            "pipeline-sha256",
            "--no-interpolation",
            "--reject-secrets",
            "--reject-parse-warnings",
        ):
            self.assertIn(expected, text)
        for forbidden in ("git -C", "checkout-deploy-key", "artifact upload"):
            self.assertNotIn(forbidden, text)

    def test_exact_checkout_is_reverified(self) -> None:
        verify = VERIFY.read_text(encoding="utf-8")
        self.assertIn("git@github.com:1-vps/hostpanel.git", verify)
        self.assertIn("git rev-parse HEAD", verify)
        self.assertIn("git status --porcelain=v1 --untracked-files=all", verify)
        self.assertRegex(verify, r"BUILDKITE_COMMIT.*40")
        hook = PRIVATE_CHECKOUT.read_text(encoding="utf-8")
        self.assertIn("checked-out repository commit mismatch", hook)
        self.assertIn("checked-out repository origin mismatch", hook)
        self.assertIn("checked-out repository is dirty", hook)
        self.assertIn("--depth=1 origin \"$commit\"", hook)

    def test_provisioning_verifier_isolates_git_environment(self) -> None:
        text = PROVENANCE.read_text(encoding="utf-8")
        for expected in (
            '"HOME": "/nonexistent"',
            '"GIT_CONFIG_GLOBAL": "/dev/null"',
            '"GIT_CONFIG_SYSTEM": "/dev/null"',
            '"GIT_CONFIG_NOSYSTEM": "1"',
            'env=git_environment()',
            '"core.hooksPath=/dev/null"',
        ):
            self.assertIn(expected, text)
        for forbidden in ("os.environ.copy()", "GIT_DIR=", "GIT_WORK_TREE="):
            self.assertNotIn(forbidden, text)

    def test_qemu_secret_policy_is_main_and_queue_bound(self) -> None:
        result = subprocess.run(
            [str(POLICY), "qemu", "--pipeline-id", PIPELINE_ID,
             "--qemu-queue-id", QEMU_QUEUE_ID],
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(yaml.safe_load(result.stdout), [{
            "pipeline_id": PIPELINE_ID,
            "build_source": "webhook",
            "build_branch": "main",
            "cluster_queue_id": QEMU_QUEUE_ID,
        }])

    def test_checkout_secret_policy_no_longer_exists(self) -> None:
        result = subprocess.run(
            [str(POLICY), "checkout", "--pipeline-id", PIPELINE_ID],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)

    @unittest.skipUnless(shutil.which("ssh-keygen"), "ssh-keygen is unavailable")
    def test_checkout_key_generator_creates_open_ssh_keypair(self) -> None:
        with tempfile.TemporaryDirectory() as parent:
            output = pathlib.Path(parent) / "keys"
            subprocess.run(
                [str(CHECKOUT_KEYS), str(output)],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            private = output / "checkout-deploy-key"
            public = output / "checkout-deploy-key.pub"
            self.assertEqual(stat.S_IMODE(private.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(public.stat().st_mode), 0o644)
            self.assertTrue(private.read_text().startswith("-----BEGIN OPENSSH PRIVATE KEY-----"))
            self.assertTrue(public.read_text().startswith("ssh-ed25519 "))

    def test_contract_runner_covers_all_buildkite_modules(self) -> None:
        text = CONTRACT.read_text(encoding="utf-8")
        for expected in ("--dry-run", "--reject-secrets", "python3 -c 'import yaml'", "yaml.safe_load"):
            self.assertIn(expected, text)
        for module in (
            "tests.test_buildkite_pipeline",
            "tests.test_buildkite_prebootstrap_policy",
            "tests.test_buildkite_checkout_policy",
            "tests.test_buildkite_private_checkout",
            "tests.test_buildkite_ephemeral_service",
            "tests.test_buildkite_provisioning_source",
            "tests.test_buildkite_agent_token",
        ):
            self.assertIn(module, text)

    def test_provisioning_and_checks_are_fail_closed(self) -> None:
        for path in (
            CONFIGURE, BOUND, CHECKOUT_POLICY, PRIVATE_CHECKOUT, TRUSTED_UPLOAD,
            VERIFY, RUN_CHECK, RUN_QEMU, CONTRACT, SMOKE, CHECKOUT_KEYS,
        ):
            text = path.read_text(encoding="utf-8")
            self.assertTrue(text.startswith("#!/usr/bin/env bash\nset -euo pipefail\n"), path)
            self.assertNotIn("set +e", text)
        self.assertTrue(PRE_BOOTSTRAP.read_text(encoding="utf-8").startswith("#!/usr/bin/env python3\n"))
        self.assertTrue(PRE_COMMAND.read_text(encoding="utf-8").startswith("#!/usr/bin/env python3\n"))

    def test_docs_and_codeowners_cover_checkout_credential_boundary(self) -> None:
        docs = DOCS.read_text(encoding="utf-8")
        for expected in (
            "3.134.0 or newer",
            "must never be stored in Buildkite Secrets",
            "checkout hook",
            "destroyed before repository code runs",
            "same-repository PR",
            "no inbound SSH",
            "restricted egress",
            "exact merged `main` commit",
            "exact-head Buildkite PR run before merge",
        ):
            self.assertIn(expected, docs)
        owners = CODEOWNERS.read_text(encoding="utf-8")
        self.assertIn("/tests/test_buildkite_private_checkout.py @1-vps", owners)

    def test_deployments_and_provider_acceptance_remain_outside_buildkite(self) -> None:
        text = PIPELINE.read_text(encoding="utf-8")
        for forbidden in ("deploy", "publish-release", "vps-acceptance", "VPS_ROOT_PASSWORD"):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
