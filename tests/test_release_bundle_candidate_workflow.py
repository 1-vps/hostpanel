from __future__ import annotations

import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE_WORKFLOW = ROOT / ".github" / "workflows" / "source-release-candidate.yml"
PUBLISH_WORKFLOW = ROOT / ".github" / "workflows" / "publish-release.yml"


class ReleaseBundleCandidateWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = SOURCE_WORKFLOW.read_text(encoding="utf-8")
        cls.publish = PUBLISH_WORKFLOW.read_text(encoding="utf-8")

    def test_pull_request_ci_builds_complete_bundle_twice(self) -> None:
        self.assertIn(
            "      - name: Build and verify the complete release bundle twice",
            self.source,
        )
        self.assertEqual(
            self.source.count("python3 tools/build-release-bundle.py"),
            2,
        )
        self.assertIn("--output-dir release-a", self.source)
        self.assertIn("--output-dir release-b", self.source)
        self.assertIn("--summary release-a-summary.json", self.source)
        self.assertIn("--summary release-b-summary.json", self.source)
        self.assertIn("cmp -- release-a-summary.json release-b-summary.json", self.source)
        self.assertIn('[[ "${#payloads[@]}" -eq 9 ]]', self.source)
        self.assertIn('cmp -- "release-a/$payload" "release-b/$payload"', self.source)
        self.assertIn('[[ "${#release_a_files[@]}" -eq 9 ]]', self.source)

    def test_pull_request_ci_uploads_the_complete_unsigned_bundle(self) -> None:
        self.assertIn(
            "      - name: Upload unsigned complete release bundle",
            self.source,
        )
        self.assertIn(
            "name: hostpanel-release-bundle-${{ github.run_id }}-${{ github.run_attempt }}",
            self.source,
        )
        self.assertIn("            release-a/*", self.source)
        self.assertIn("            release-a-summary.json", self.source)
        self.assertIn("          compression-level: 0", self.source)
        self.assertIn("          overwrite: false", self.source)

    def test_publish_rechecks_main_and_gates_immediately_before_tagging(self) -> None:
        publish_step = self.publish.index("      - name: Publish GitHub release")
        tag_branch = self.publish.index('          if [[ "$CREATE_TAG" == true ]]; then', publish_step)
        section = self.publish[publish_step:tag_branch]
        self.assertIn(
            'current_main="$(gh api "repos/$GITHUB_REPOSITORY/git/ref/heads/main" --jq .object.sha)"',
            section,
        )
        self.assertIn("Refusing to tag stale commit", section)
        self.assertIn("for gate_issue in 7 14 115 116 118 119 120 121; do", section)
        self.assertIn("changed before tag creation", section)

    def test_protected_bundle_must_match_read_only_summary(self) -> None:
        self.assertIn(
            "Protected release bundle does not reproduce the verified bundle summary.",
            self.publish,
        )
        self.assertIn(
            '[[ "$summary_sha256" == "${{ needs.verify.outputs.bundle_summary_sha256 }}" ]]',
            self.publish,
        )
        self.assertIn(
            "Protected signing key does not match releases/update.pub.",
            self.publish,
        )


if __name__ == "__main__":
    unittest.main()
