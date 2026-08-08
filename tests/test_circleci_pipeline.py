from __future__ import annotations

import pathlib
import os
import shutil
import subprocess
import tempfile
import unittest

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]
PR_CONFIG = ROOT / ".circleci" / "pr.yml"
MAIN_CONFIG = ROOT / ".circleci" / "main.yml"


class CircleCiPipelineTests(unittest.TestCase):
    def load(self, path: pathlib.Path) -> dict:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        self.assertIsInstance(payload, dict)
        self.assertEqual(payload.get("version"), 2.1)
        return payload

    def test_configs_exclude_unapproved_data_paths(self) -> None:
        forbidden = (
            "store_test_results",
            "persist_to_workspace",
            "attach_workspace",
            "save_cache",
            "restore_cache",
            "add_ssh_keys",
            "context:",
            "pipeline.trigger_parameters.github_app",
        )
        for path in (PR_CONFIG, MAIN_CONFIG):
            text = path.read_text(encoding="utf-8")
            for value in forbidden:
                self.assertNotIn(value, text, f"{path.name} contains {value}")
            self.assertIn("PRIVATE KEY", text)
            self.assertIn("refs/heads/main", text)
            self.assertNotIn("python3 - <<", text)
            self.assertNotIn('cat >"$BASH_ENV" <<', text)

        pr_text = PR_CONFIG.read_text(encoding="utf-8")
        main_text = MAIN_CONFIG.read_text(encoding="utf-8")
        self.assertNotIn("store_artifacts", pr_text)
        self.assertEqual(main_text.count("store_artifacts"), 1)
        self.assertIn("path: artifacts/qemu-vm-acceptance.tar", main_text)
        self.assertIn("/tmp/hostpanel-circleci-qemu-status", main_text)
        self.assertIn("Enforce QEMU acceptance result", main_text)

    def test_configs_compile_with_circleci_cli_when_available(self) -> None:
        cli = shutil.which("circleci")
        if cli is None:
            self.skipTest("CircleCI CLI is unavailable")
        with tempfile.TemporaryDirectory() as config_home:
            env = os.environ.copy()
            env["XDG_CONFIG_HOME"] = config_home
            for path in (PR_CONFIG, MAIN_CONFIG):
                completed = subprocess.run(
                    [cli, "config", "validate", "--config", str(path)],
                    check=False,
                    capture_output=True,
                    env=env,
                    text=True,
                    timeout=30,
                )
                self.assertEqual(
                    completed.returncode,
                    0,
                    f"{path.name} failed CircleCI validation:\n"
                    f"{completed.stdout}{completed.stderr}",
                )

    def test_pr_pipeline_has_exact_job_graph(self) -> None:
        payload = self.load(PR_CONFIG)
        jobs = payload["jobs"]
        self.assertEqual(
            set(jobs),
            {
                "pipeline-contract",
                "repository-regressions",
                "installer-static",
                "release-metadata",
                "production-validator",
                "qemu-contracts",
                "installer-os",
            },
        )
        for job in jobs.values():
            self.assertTrue(job["machine"])
            self.assertEqual(job["resource_class"], "1-vps/hostpanel-ci")
        workflow = payload["workflows"]["exact-pr-head"]["jobs"]
        rendered = yaml.safe_dump(workflow)
        for target in (
            "ubuntu-22.04",
            "ubuntu-24.04",
            "ubuntu-26.04",
            "debian-12",
            "debian-13",
            "rocky-9",
            "rocky-10",
            "almalinux-9",
            "almalinux-10",
        ):
            self.assertIn(target, rendered)
        self.assertNotIn("qemu-vm-acceptance", rendered)

    def test_main_pipeline_adds_only_qemu_acceptance(self) -> None:
        payload = self.load(MAIN_CONFIG)
        jobs = payload["jobs"]
        self.assertEqual(jobs["qemu-vm-acceptance"]["resource_class"], "1-vps/hostpanel-qemu")
        self.assertEqual(jobs["installer-os"]["resource_class"], "1-vps/hostpanel-ci")
        rendered = yaml.safe_dump(payload["workflows"]["exact-main-head"]["jobs"])
        self.assertIn("qemu-vm-acceptance", rendered)
        self.assertIn("os-almalinux-10", rendered)

    def test_exact_head_and_config_source_are_bound(self) -> None:
        pr = PR_CONFIG.read_text(encoding="utf-8")
        for value in (
            "pipeline.config.repository.name",
            "pipeline.config.file_path",
            "pipeline.config.ref",
            "pipeline.config.sha",
            "pipeline.git.revision",
            "pipeline.event.github.pull_request.head.sha",
            "pipeline.event.github.pull_request.base.sha",
            "pipeline.event.github.pull_request.number",
            "config SHA is not the exact PR base SHA",
            "checkout revision is not the PR head SHA",
        ):
            self.assertIn(value, pr)
        main = MAIN_CONFIG.read_text(encoding="utf-8")
        for value in (
            "pipeline.git.branch.is_default",
            "pipeline.event.github.after",
            "config, checkout and push SHAs differ",
        ):
            self.assertIn(value, main)

    def test_timeouts_are_fail_closed(self) -> None:
        combined = PR_CONFIG.read_text(encoding="utf-8") + MAIN_CONFIG.read_text(encoding="utf-8")
        for duration in ("10m", "75m", "30m", "15m", "20m", "35m", "90m", "180m"):
            self.assertIn(f"timeout --signal=TERM --kill-after=30s {duration}", combined)

    def test_qemu_uses_only_worker_local_token(self) -> None:
        script = (ROOT / ".circleci" / "scripts" / "run-qemu.sh").read_text(encoding="utf-8")
        self.assertIn("/run/hostpanel-circleci/qemu-repo-token", script)
        self.assertIn('rm -f -- "$token_file"', script)
        self.assertNotIn("CIRCLE_TOKEN", script)
        self.assertNotIn("circleci-agent artifact", script)
        self.assertNotIn("curl --upload-file", script)
        self.assertIn("trap cleanup_secret EXIT", script)
        self.assertIn("archive_size <= 1073741824", script)

    def test_checkout_cleanup_covers_github_app_https_credentials(self) -> None:
        for path in (PR_CONFIG, MAIN_CONFIG):
            text = path.read_text(encoding="utf-8")
            for value in (
                "credential\\.helper",
                "http\\..*\\.extraheader",
                'GIT_CONFIG_GLOBAL=/dev/null',
                "GIT_CONFIG_NOSYSTEM=1",
                "GIT_TERMINAL_PROMPT=0",
                "unset GIT_ASKPASS SSH_ASKPASS SSH_AUTH_SOCK SSH_AGENT_PID",
                '$HOME/.git-credentials',
                '$HOME/.config/git/credentials',
            ):
                self.assertIn(value, text, f"{path.name} lacks cleanup for {value}")


if __name__ == "__main__":
    unittest.main()
