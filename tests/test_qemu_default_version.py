import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
HARNESS = ROOT / "tools" / "run-qemu-vm-acceptance.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "qemu-vm-acceptance.yml"


class QemuDefaultVersionTests(unittest.TestCase):
    def test_local_harness_default_matches_ci_release_version(self):
        harness = HARNESS.read_text(encoding="utf-8")
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn(
            'EXPECTED_VERSION="${HP_QEMU_EXPECTED_VERSION:-3.4.0}"',
            harness,
        )
        self.assertIn("HP_QEMU_EXPECTED_VERSION: 3.4.0", workflow)
        self.assertNotIn("3.4.0-hardened-r6", harness)

    def test_success_evidence_omits_generic_unredacted_journals(self):
        harness = HARNESS.read_text(encoding="utf-8")
        self.assertNotIn("guest-failure-diagnostics.txt", harness)
        self.assertNotIn("journalctl -b -p warning..alert", harness)
        self.assertIn("extract_guest_evidence", harness)


if __name__ == "__main__":
    unittest.main()
