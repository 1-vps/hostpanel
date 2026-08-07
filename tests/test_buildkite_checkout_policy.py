from __future__ import annotations

import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
HOOK = ROOT / ".buildkite" / "agent" / "environment-checkout-policy"
CONTRACT = ROOT / ".buildkite" / "scripts" / "run-pipeline-contract.sh"
CODEOWNERS = ROOT / ".github" / "CODEOWNERS"


class BuildkiteCheckoutPolicyTests(unittest.TestCase):
    def run_hook(self, queue: str, *, mode: int = 0o440) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            policy = root / "queue"
            policy.write_text(f"{queue}\n", encoding="utf-8")
            policy.chmod(mode)
            rendered = root / "environment"
            rendered_source = (
                HOOK.read_text(encoding="utf-8")
                .replace("/etc/buildkite-agent/hostpanel-policy/queue", str(policy))
                .replace("info.st_uid != 0", "info.st_uid != os.getuid()")
            )
            rendered.write_text(rendered_source, encoding="utf-8")
            rendered.chmod(0o755)
            return subprocess.run(
                [
                    "bash",
                    "-c",
                    'source "$1"; printf "%s|%s" '
                    '"${BUILDKITE_SKIP_CHECKOUT-<unset>}" '
                    '"${BUILDKITE_CLEAN_CHECKOUT-<unset>}"',
                    "bash",
                    str(rendered),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

    def test_upload_queue_forces_no_checkout(self) -> None:
        completed = self.run_hook("hostpanel-upload")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "true|<unset>")

    def test_runner_queues_force_fresh_checkout(self) -> None:
        for queue in ("hostpanel-ci", "hostpanel-qemu"):
            with self.subTest(queue=queue):
                completed = self.run_hook(queue)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(completed.stdout, "false|true")

    def test_unknown_or_writable_policy_is_rejected(self) -> None:
        unknown = self.run_hook("hostpanel-unknown")
        self.assertNotEqual(unknown.returncode, 0)
        self.assertIn("queue is unknown", unknown.stderr)

        writable = self.run_hook("hostpanel-ci", mode=0o660)
        self.assertNotEqual(writable.returncode, 0)
        self.assertIn("ownership or mode is unsafe", writable.stderr)

    def test_contract_and_codeowners_cover_checkout_policy(self) -> None:
        self.assertIn(
            "tests.test_buildkite_checkout_policy",
            CONTRACT.read_text(encoding="utf-8"),
        )
        self.assertIn(
            "/tests/test_buildkite_checkout_policy.py @1-vps",
            CODEOWNERS.read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
