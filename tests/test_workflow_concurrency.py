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
        self.assertIn(
            "${{ github.event.pull_request.number || github.ref }}",
            before_jobs,
            workflow,
        )
        self.assertIn("cancel-in-progress: true", before_jobs, workflow)
        self.assertNotIn("github.sha", before_jobs, workflow)
        self.assertNotIn("cancel-in-progress: false", before_jobs, workflow)

    def test_installer_uses_pr_ref_concurrency(self) -> None:
        self.assert_pr_ref_concurrency(self.installer, "installer-hardening")

    def test_qemu_uses_pr_ref_concurrency(self) -> None:
        self.assert_pr_ref_concurrency(self.qemu, "qemu-vm-acceptance")

    def test_installer_uses_fixed_runners_and_timeouts(self) -> None:
        self.assertNotIn("ubuntu-latest", self.installer)
        self.assertEqual(self.installer.count("runs-on: ubuntu-24.04"), 3)
        self.assertEqual(self.installer.count("timeout-minutes:"), 3)


if __name__ == "__main__":
    unittest.main()
