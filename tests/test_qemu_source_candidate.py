from __future__ import annotations

import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / "tools" / "bootstrap-source-candidate.sh"
WRAPPER = ROOT / "tools" / "run-qemu-source-candidate.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "qemu-vm-acceptance.yml"
SOURCE_VERSION = (ROOT / "SOURCE_VERSION").read_text(encoding="utf-8").strip()


class QemuSourceCandidateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bootstrap = BOOTSTRAP.read_text(encoding="utf-8")
        cls.wrapper = WRAPPER.read_text(encoding="utf-8")
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_candidate_bootstrap_is_commit_bound_and_secretless(self) -> None:
        self.assertIn('[[ "$REF" =~ ^[0-9a-fA-F]{40}$ ]]', self.bootstrap)
        self.assertIn("git -C \"$CHECKOUT\" fetch --depth 1 origin \"$REF\"", self.bootstrap)
        self.assertIn('[[ "${FETCHED_COMMIT,,}" == "$REF" ]]', self.bootstrap)
        self.assertIn("git -C \"$CHECKOUT\" diff --exit-code -- .", self.bootstrap)
        self.assertNotIn("secrets.", self.bootstrap)
        self.assertNotIn("PRIVATE KEY", self.bootstrap)
        self.assertNotIn("curl |", self.bootstrap)

    def test_candidate_bootstrap_reproduces_and_verifies_source_release(self) -> None:
        self.assertIn("tools/run-source-release-builder.py", self.bootstrap)
        self.assertIn("--commit \"$REF\"", self.bootstrap)
        self.assertIn("source-build.json is not bound to the reviewed commit", self.bootstrap)
        self.assertIn("source archive digest does not match provenance", self.bootstrap)
        self.assertIn("sbom.cdx.json", self.bootstrap)
        self.assertIn("dependency-lock-attestation.json", self.bootstrap)
        self.assertIn("source-file-manifest.json", self.bootstrap)
        self.assertIn("unsupported candidate archive member", self.bootstrap)
        self.assertIn("duplicate candidate archive path", self.bootstrap)
        self.assertIn("Extracted candidate VERSION does not match SOURCE_VERSION", self.bootstrap)

    def test_wrapper_temporarily_promotes_and_restores_candidate_bootstrap(self) -> None:
        self.assertIn("ORIGINAL_SHA=", self.wrapper)
        self.assertIn("restore_bootstrap", self.wrapper)
        self.assertIn("production bootstrap restoration failed", self.wrapper)
        self.assertIn('install -m 0755 "$CANDIDATE_BOOTSTRAP" "$PRODUCTION_BOOTSTRAP"', self.wrapper)
        self.assertIn('export HP_QEMU_EXPECTED_VERSION="$SOURCE_VERSION"', self.wrapper)
        self.assertIn('bash "$HARNESS"', self.wrapper)
        self.assertNotIn("exec bash", self.wrapper)

    def test_workflow_uses_candidate_wrapper_and_version_inputs(self) -> None:
        self.assertIn("- SOURCE_VERSION", self.workflow)
        self.assertIn("- RELEASE_VERSION", self.workflow)
        self.assertIn("- tools/bootstrap-source-candidate.sh", self.workflow)
        self.assertIn("- tools/run-qemu-source-candidate.sh", self.workflow)
        self.assertIn("bash -n tools/bootstrap-source-candidate.sh", self.workflow)
        self.assertIn("bash -n tools/run-qemu-source-candidate.sh", self.workflow)
        self.assertIn("bash tools/run-qemu-source-candidate.sh", self.workflow)
        self.assertIn("github.event.pull_request.head.sha || github.sha", self.workflow)

    def test_source_version_is_patch_release(self) -> None:
        self.assertEqual(SOURCE_VERSION, "3.4.1")


if __name__ == "__main__":
    unittest.main()
