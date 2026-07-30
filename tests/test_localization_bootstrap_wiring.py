import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / "bootstrap-install.sh"
LOCALIZATION_WORKFLOW = ROOT / ".github" / "workflows" / "localization-overlay.yml"
QEMU_WORKFLOW = ROOT / ".github" / "workflows" / "qemu-vm-acceptance.yml"
WRAPPER = "localization-overlay/apply_localization_overlay_reviewed.py"
FINAL_OVERRIDE_FILES = (
    "localization-overlay/catalog-final-overrides.ja-01.json",
    "localization-overlay/catalog-final-overrides.pt-01.json",
    "localization-overlay/catalog-final-overrides.zh-01.json",
    "localization-overlay/catalog-final-overrides.zh-02.json",
)


class LocalizationBootstrapWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bootstrap = BOOTSTRAP.read_text(encoding="utf-8")
        cls.localization_workflow = LOCALIZATION_WORKFLOW.read_text(encoding="utf-8")
        cls.qemu_workflow = QEMU_WORKFLOW.read_text(encoding="utf-8")

    def test_bootstrap_verifies_and_invokes_reviewed_wrapper(self):
        self.assertIn(f"  {WRAPPER}\n", self.bootstrap)
        self.assertIn(
            'python3 "$LOCALIZATION_ROOT/apply_localization_overlay_reviewed.py"',
            self.bootstrap,
        )
        self.assertNotIn(
            'python3 "$LOCALIZATION_ROOT/apply_localization_overlay.py"',
            self.bootstrap,
        )
        for path in FINAL_OVERRIDE_FILES:
            with self.subTest(path=path):
                self.assertEqual(self.bootstrap.count(path), 1)

    def test_localization_workflow_uses_wrapper_and_runs_override_regression(self):
        self.assertIn(
            "python3 localization-overlay/apply_localization_overlay_reviewed.py",
            self.localization_workflow,
        )
        self.assertIn("test_high_risk_locale_overrides.py", self.localization_workflow)
        self.assertIn("apply_localization_overlay_reviewed.py", self.localization_workflow)

    def test_qemu_runs_for_localization_changes(self):
        self.assertIn("      - localization-overlay/**", self.qemu_workflow)
        self.assertIn("      - tests/test_high_risk_locale_overrides.py", self.qemu_workflow)
        self.assertIn("test_high_risk_locale_overrides.py", self.qemu_workflow)


if __name__ == "__main__":
    unittest.main()
