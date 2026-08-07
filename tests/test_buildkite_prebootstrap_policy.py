from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import pathlib
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
HOOK = ROOT / ".buildkite" / "agent" / "pre-bootstrap"
PIPELINE_ID = "11111111-1111-4111-8111-111111111111"
PIPELINE_SLUG = "hostpanel"


def load_hook_module():
    loader = importlib.machinery.SourceFileLoader("hostpanel_prebootstrap", str(HOOK))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise RuntimeError("could not create pre-bootstrap module spec")
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class BuildkitePreBootstrapPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.hook = load_hook_module()

    def base_payload(self) -> dict[str, str]:
        return {
            "BUILDKITE_REPO": "git@github.com:1-vps/hostpanel.git",
            "BUILDKITE_COMMAND": ".buildkite/scripts/run-check.sh",
            "BUILDKITE_PIPELINE_SLUG": PIPELINE_SLUG,
            "BUILDKITE_PIPELINE_ID": PIPELINE_ID,
            "BUILDKITE_STEP_KEY": "repository-regressions",
            "BUILDKITE_BRANCH": "agent/add-buildkite-ci",
            "BUILDKITE_PULL_REQUEST": "161",
            "BUILDKITE_PULL_REQUEST_REPO": "https://github.com/1-vps/hostpanel.git",
            "BUILDKITE_SOURCE": "webhook",
            "BUILDKITE_PLUGINS": "[]",
            "BUILDKITE_CLEAN_CHECKOUT": "true",
            "BUILDKITE_SKIP_CHECKOUT": "false",
            "HP_BUILDKITE_CHECK": "repository",
            "HP_BUILDKITE_OS": "",
        }

    def run_policy(
        self,
        payload: dict[str, str],
        *,
        queue: str = "hostpanel-ci",
    ) -> int:
        policies = {
            "queue": queue,
            "pipeline-slug": PIPELINE_SLUG,
            "pipeline-id": PIPELINE_ID,
        }
        with tempfile.TemporaryDirectory() as directory:
            env_file = pathlib.Path(directory) / "job-env.json"
            env_file.write_text(json.dumps(payload), encoding="utf-8")
            env_file.chmod(0o600)
            with (
                mock.patch.object(
                    self.hook,
                    "read_policy",
                    side_effect=lambda name: policies[name],
                ),
                mock.patch.dict(
                    os.environ,
                    {"BUILDKITE_ENV_JSON_FILE": str(env_file)},
                    clear=False,
                ),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                return self.hook.main()

    def assert_rejected(self, payload: dict[str, str], message: str, **kwargs) -> None:
        with self.assertRaisesRegex(SystemExit, message):
            self.run_policy(payload, **kwargs)

    def test_accepts_trusted_upload_without_checkout(self) -> None:
        payload = self.base_payload()
        payload.update(
            {
                "BUILDKITE_COMMAND": "/usr/local/libexec/hostpanel-upload-pipeline",
                "BUILDKITE_STEP_KEY": "upload-reviewed-pipeline",
                "BUILDKITE_CLEAN_CHECKOUT": "false",
                "BUILDKITE_SKIP_CHECKOUT": "true",
                "HP_BUILDKITE_CHECK": "",
                "HP_BUILDKITE_OS": "",
            }
        )
        self.assertEqual(self.run_policy(payload, queue="hostpanel-upload"), 0)

    def test_rejects_upload_queue_repository_checkout(self) -> None:
        payload = self.base_payload()
        payload.update(
            {
                "BUILDKITE_COMMAND": "/usr/local/libexec/hostpanel-upload-pipeline",
                "BUILDKITE_STEP_KEY": "upload-reviewed-pipeline",
                "BUILDKITE_CLEAN_CHECKOUT": "true",
                "BUILDKITE_SKIP_CHECKOUT": "false",
                "HP_BUILDKITE_CHECK": "",
                "HP_BUILDKITE_OS": "",
            }
        )
        self.assert_rejected(payload, "must skip", queue="hostpanel-upload")

    def test_accepts_same_repository_pull_request(self) -> None:
        self.assertEqual(self.run_policy(self.base_payload()), 0)

    def test_accepts_supported_same_repository_url_forms(self) -> None:
        expected = ("1-vps", "hostpanel")
        for repository in (
            "https://github.com/1-vps/hostpanel",
            "https://github.com/1-vps/hostpanel.git",
            "git://github.com/1-vps/hostpanel.git",
            "git@github.com:1-vps/hostpanel.git",
            "ssh://git@github.com/1-vps/hostpanel.git",
        ):
            with self.subTest(repository=repository):
                self.assertEqual(self.hook.github_repository_identity(repository), expected)

    def test_rejects_pull_request_from_fork(self) -> None:
        payload = self.base_payload()
        payload["BUILDKITE_PULL_REQUEST_REPO"] = "https://github.com/attacker/hostpanel.git"
        self.assert_rejected(payload, "unreviewed repository")

    def test_rejects_non_webhook_build(self) -> None:
        payload = self.base_payload()
        payload["BUILDKITE_SOURCE"] = "api"
        self.assert_rejected(payload, "webhook-triggered")

    def test_rejects_reused_checkout(self) -> None:
        payload = self.base_payload()
        payload["BUILDKITE_CLEAN_CHECKOUT"] = "false"
        self.assert_rejected(payload, "CLEAN_CHECKOUT")

    def test_rejects_skipped_checkout(self) -> None:
        payload = self.base_payload()
        payload["BUILDKITE_SKIP_CHECKOUT"] = "true"
        self.assert_rejected(payload, "may not be skipped")

    def test_rejects_dangerous_environment_injection(self) -> None:
        payload = self.base_payload()
        payload["BASH_ENV"] = "/tmp/attacker.sh"
        self.assert_rejected(payload, "BASH_ENV")

    def test_rejects_step_environment_mismatch(self) -> None:
        payload = self.base_payload()
        payload["HP_BUILDKITE_CHECK"] = "shell"
        self.assert_rejected(payload, "does not match")

    def test_accepts_exact_os_step_mapping(self) -> None:
        payload = self.base_payload()
        payload.update(
            {
                "BUILDKITE_STEP_KEY": "os-ubuntu-2404",
                "HP_BUILDKITE_CHECK": "installer-os",
                "HP_BUILDKITE_OS": "ubuntu-24.04",
            }
        )
        self.assertEqual(self.run_policy(payload), 0)

    def test_rejects_wrong_os_for_step_key(self) -> None:
        payload = self.base_payload()
        payload.update(
            {
                "BUILDKITE_STEP_KEY": "os-ubuntu-2404",
                "HP_BUILDKITE_CHECK": "installer-os",
                "HP_BUILDKITE_OS": "rocky-10",
            }
        )
        self.assert_rejected(payload, "does not match")

    def test_accepts_webhook_main_qemu_job_without_pr_metadata(self) -> None:
        payload = self.base_payload()
        payload.update(
            {
                "BUILDKITE_COMMAND": ".buildkite/scripts/run-qemu.sh",
                "BUILDKITE_STEP_KEY": "qemu-vm-acceptance",
                "BUILDKITE_BRANCH": "main",
                "BUILDKITE_PULL_REQUEST": "false",
                "BUILDKITE_PULL_REQUEST_REPO": "",
                "HP_BUILDKITE_CHECK": "",
                "HP_BUILDKITE_OS": "",
            }
        )
        self.assertEqual(self.run_policy(payload, queue="hostpanel-qemu"), 0)

    def test_rejects_qemu_environment_injection(self) -> None:
        payload = self.base_payload()
        payload["HP_QEMU_MTA"] = "exim"
        self.assert_rejected(payload, "QEMU environment injection")


if __name__ == "__main__":
    unittest.main()
