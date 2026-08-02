from __future__ import annotations

import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "publish-release.yml"


class ReleaseWorkflowPermissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_verify_job_can_read_release_gate_issues(self) -> None:
        self.assertIn(
            "permissions:\n  contents: read\n  issues: read",
            self.workflow,
        )

    def test_publish_job_keeps_write_scope_narrow(self) -> None:
        self.assertIn(
            "permissions:\n      contents: write\n      issues: read",
            self.workflow,
        )
        self.assertNotIn("issues: write", self.workflow)
        self.assertNotIn("actions: write", self.workflow)
        self.assertNotIn("pull-requests: write", self.workflow)


if __name__ == "__main__":
    unittest.main()