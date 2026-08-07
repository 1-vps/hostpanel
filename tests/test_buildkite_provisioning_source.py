from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
VERIFIER = ROOT / ".buildkite/operator/verify-provisioning-source.py"
BOUND = ROOT / ".buildkite/agent/configure-bound-agent.sh"
CONTRACT = ROOT / ".buildkite/scripts/run-pipeline-contract.sh"
EXPECTED_ORIGIN = "git@github.com:1-vps/hostpanel.git"
SOURCE_PATH = ".buildkite/agent/configure-agent.sh"


class BuildkiteProvisioningSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.repository = pathlib.Path(self.temporary.name) / "repository"
        self.repository.mkdir()
        self.git("init", "-b", "main")
        self.git("config", "user.name", "HostPanel Test")
        self.git("config", "user.email", "hostpanel-test@example.invalid")
        self.git("remote", "add", "origin", EXPECTED_ORIGIN)

        helper = self.repository / ".buildkite/operator/verify-provisioning-source.py"
        source = self.repository / SOURCE_PATH
        helper.parent.mkdir(parents=True)
        source.parent.mkdir(parents=True)
        shutil.copyfile(VERIFIER, helper)
        helper.chmod(0o755)
        source.write_text("#!/usr/bin/env bash\nset -euo pipefail\n", encoding="utf-8")
        source.chmod(0o755)
        self.git("add", ".")
        self.git("commit", "-m", "Add reviewed provisioning source")
        self.commit = self.git("rev-parse", "HEAD")
        self.git("update-ref", "refs/remotes/origin/main", self.commit)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def git(self, *args: str, check: bool = True) -> str:
        result = subprocess.run(
            ["/usr/bin/git", "-C", str(self.repository), *args],
            check=check,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return result.stdout.strip()

    def verify(
        self,
        *,
        expected_commit: str | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            [
                "/usr/bin/python3",
                str(self.repository / ".buildkite/operator/verify-provisioning-source.py"),
                "--repository",
                str(self.repository),
                "--expected-commit",
                expected_commit or self.commit,
                "--source-path",
                SOURCE_PATH,
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )

    def test_accepts_exact_clean_origin_main_commit(self) -> None:
        result = self.verify()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), self.commit)

    def test_rejects_local_head_and_origin_main_when_external_commit_differs(self) -> None:
        result = self.verify(expected_commit="0" * 40)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("externally reviewed commit", result.stderr)

    def test_ignores_ambient_git_configuration_and_repository_overrides(self) -> None:
        hostile = self.repository.parent / "hostile-gitconfig"
        hostile.write_text(
            '[url "git@github.com:attacker/"]\n'
            '    insteadOf = git@github.com:1-vps/\n',
            encoding="utf-8",
        )
        result = self.verify(
            extra_env={
                "GIT_CONFIG_GLOBAL": str(hostile),
                "GIT_DIR": str(self.repository.parent / "not-a-repository"),
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "url.git@github.com:attacker/.insteadOf",
                "GIT_CONFIG_VALUE_0": "git@github.com:1-vps/",
                "GIT_NO_REPLACE_OBJECTS": "0",
            }
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), self.commit)

    def test_disables_repo_local_fsmonitor_hook_during_root_checks(self) -> None:
        marker = self.repository.parent / "fsmonitor-ran"
        hook = self.repository.parent / "fsmonitor-hook"
        hook.write_text(
            "#!/usr/bin/env bash\n"
            f"printf ran > {marker}\n"
            "printf '{}\\n'\n",
            encoding="utf-8",
        )
        hook.chmod(0o755)
        self.git("config", "core.fsmonitor", str(hook))
        result = self.verify()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(marker.exists(), "repo-local core.fsmonitor executed during verification")

    def test_disables_git_replacement_objects_for_reviewed_commit(self) -> None:
        source = self.repository / SOURCE_PATH
        source.write_text("#!/usr/bin/env bash\nprintf attacker\\n\n", encoding="utf-8")
        self.git("add", SOURCE_PATH)
        self.git("commit", "-m", "Create attacker replacement tree")
        attacker_commit = self.git("rev-parse", "HEAD")
        self.git("reset", "--hard", self.commit)
        self.git("update-ref", "refs/remotes/origin/main", self.commit)
        self.git("replace", self.commit, attacker_commit)
        source.write_text("#!/usr/bin/env bash\nprintf attacker\\n\n", encoding="utf-8")

        result = self.verify()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not match the reviewed commit", result.stderr)

    def test_rejects_wrong_origin(self) -> None:
        self.git("remote", "set-url", "origin", "git@github.com:attacker/hostpanel.git")
        result = self.verify()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("origin is not the reviewed", result.stderr)

    def test_rejects_remote_main_mismatch(self) -> None:
        (self.repository / "second.txt").write_text("second\n", encoding="utf-8")
        self.git("add", "second.txt")
        self.git("commit", "-m", "Advance local main")
        new_commit = self.git("rev-parse", "HEAD")
        result = self.verify(expected_commit=new_commit)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("HEAD does not equal refs/remotes/origin/main", result.stderr)

    def test_rejects_dirty_worktree(self) -> None:
        (self.repository / "untracked.txt").write_text("dirty\n", encoding="utf-8")
        result = self.verify()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("worktree is not clean", result.stderr)

    def test_rejects_modified_source_even_when_assume_unchanged(self) -> None:
        self.git("update-index", "--assume-unchanged", SOURCE_PATH)
        (self.repository / SOURCE_PATH).write_text("changed\n", encoding="utf-8")
        result = self.verify()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not match the reviewed commit", result.stderr)

    def test_rejects_modified_verifier(self) -> None:
        helper = self.repository / ".buildkite/operator/verify-provisioning-source.py"
        helper.write_text(helper.read_text(encoding="utf-8") + "# changed\n", encoding="utf-8")
        result = self.verify()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("verify-provisioning-source.py does not match", result.stderr)

    def test_bound_provisioner_materializes_all_root_sources_before_execution(self) -> None:
        text = BOUND.read_text(encoding="utf-8")
        self.assertIn("GIT_NO_REPLACE_OBJECTS=1", text)
        self.assertIn('trusted_source_paths=("$source_verifier_relative" "${source_paths[@]}")', text)
        self.assertIn("/run/hostpanel-buildkite-source.XXXXXX", text)
        self.assertIn('for source_path in "${trusted_source_paths[@]}"; do', text)
        self.assertIn('trusted_git cat-file blob "$reviewed_blob" >"$trusted_path"', text)
        self.assertIn("materialized provisioning source does not match reviewed commit", text)
        snapshot_loop = text.index('for source_path in "${trusted_source_paths[@]}"; do')
        verifier_exec = text.index('/usr/bin/python3 "$trusted_verifier" "${verify_args[@]}"')
        base_exec = text.index('"$trusted_agent_script" "${base_args[@]}"')
        self.assertLess(snapshot_loop, verifier_exec)
        self.assertLess(verifier_exec, base_exec)
        self.assertNotIn('"$script_dir/configure-agent.sh" "${base_args[@]}"', text)
        self.assertIn('"$trusted_agent_dir/pre-command-pipeline-id"', text)
        self.assertIn('"$trusted_agent_dir/30-hostpanel-ephemeral.conf"', text)
        self.assertNotIn('"$script_dir/pre-command-pipeline-id"', text)
        self.assertNotIn('"$script_dir/30-hostpanel-ephemeral.conf"', text)
        self.assertEqual(text.count('/usr/bin/python3 "$trusted_verifier" "${verify_args[@]}"'), 2)
        self.assertIn("no repository-worktree file is executed or installed as", text)

    def test_bound_provisioner_verifies_before_and_after_installation(self) -> None:
        text = BOUND.read_text(encoding="utf-8")
        self.assertIn("--source-commit", text)
        self.assertIn('--expected-commit "$source_commit_expected"', text)
        self.assertIn("externally reviewed full lowercase 40-hex commit SHA", text)
        self.assertIn("source commit changed during provisioning", text)
        self.assertGreater(
            text.index('verified_after="$(/usr/bin/python3'),
            text.index('/etc/systemd/system/buildkite-agent.service.d/30-hostpanel-ephemeral.conf'),
        )
        self.assertIn("installed pre-command hook does not match", text)
        self.assertIn("installed ephemeral service policy does not match", text)
        self.assertIn("buildkite-agent:buildkite-agent:700", text)
        for source_path in (
            ".buildkite/agent/configure-agent.sh",
            ".buildkite/agent/configure-bound-agent.sh",
            ".buildkite/agent/pre-bootstrap",
            ".buildkite/agent/environment-checkout-policy",
            ".buildkite/agent/checkout-private-repo",
            ".buildkite/agent/pre-command-pipeline-id",
            ".buildkite/agent/upload-trusted-pipeline.sh",
            ".buildkite/agent/30-hostpanel-ephemeral.conf",
            ".buildkite/pipeline.yml",
        ):
            self.assertIn(source_path, text)
        self.assertIn("source-commit", text)

    def test_pipeline_contract_runs_provenance_tests(self) -> None:
        self.assertIn(
            "tests.test_buildkite_provisioning_source",
            CONTRACT.read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
