#!/usr/bin/env python3
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
INSTALLER = ROOT / ".github" / "workflows" / "installer-hardening.yml"
QEMU = ROOT / ".github" / "workflows" / "qemu-vm-acceptance.yml"


class WorkflowConcurrencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.installer = INSTALLER.read_text(encoding="utf-8")
        cls.qemu = QEMU.read_text(encoding="utf-8")

    def assert_pr_ref_concurrency(self, text: str, workflow: str) -> None:
        before_jobs = text.split("\njobs:\n", 1)[0]
        concurrency = before_jobs.split("\nconcurrency:\n", 1)[1]
        self.assertIn(
            "${{ github.event.pull_request.number || github.ref }}",
            concurrency,
            workflow,
        )
        self.assertIn("cancel-in-progress: true", concurrency, workflow)
        self.assertNotIn("github.sha", concurrency, workflow)
        self.assertNotIn("cancel-in-progress: false", concurrency, workflow)

    def test_installer_uses_pr_ref_concurrency(self) -> None:
        self.assert_pr_ref_concurrency(self.installer, "installer-hardening")

    def test_qemu_uses_pr_ref_concurrency(self) -> None:
        self.assert_pr_ref_concurrency(self.qemu, "qemu-vm-acceptance")

    def test_installer_uses_fixed_runners_and_timeouts(self) -> None:
        self.assertNotIn("ubuntu-latest", self.installer)
        self.assertEqual(self.installer.count("runs-on: ubuntu-24.04"), 3)
        self.assertEqual(self.installer.count("timeout-minutes:"), 3)

    def test_installer_jobs_use_and_verify_the_exact_reviewed_commit(self) -> None:
        self.assertIn(
            "REVIEWED_COMMIT: ${{ github.event.pull_request.head.sha || github.sha }}",
            self.installer,
        )
        self.assertEqual(
            self.installer.count("          ref: ${{ env.REVIEWED_COMMIT }}"),
            3,
        )
        self.assertEqual(
            self.installer.count("      - name: Verify exact reviewed checkout"),
            3,
        )
        self.assertEqual(
            self.installer.count('[[ "$REVIEWED_COMMIT" =~ ^[0-9a-f]{40}$ ]]'),
            3,
        )
        self.assertEqual(
            self.installer.count('[[ "$(git rev-parse HEAD)" == "$REVIEWED_COMMIT" ]]'),
            3,
        )
        self.assertEqual(
            self.installer.count("          git diff --exit-code -- ."),
            3,
        )
        first_verification = self.installer.index(
            "      - name: Verify exact reviewed checkout"
        )
        first_installer_command = self.installer.index(
            "      - name: Generate the exact hardened installer"
        )
        self.assertLess(first_verification, first_installer_command)


if __name__ == "__main__":
    unittest.main()
