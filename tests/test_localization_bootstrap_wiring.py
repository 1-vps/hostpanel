import importlib.util
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / "bootstrap-install.sh"
LOCALIZATION_WORKFLOW = ROOT / ".github" / "workflows" / "localization-overlay.yml"
QEMU_WORKFLOW = ROOT / ".github" / "workflows" / "qemu-vm-acceptance.yml"
WRAPPER_PATH = ROOT / "localization-overlay" / "apply_localization_overlay_reviewed.py"
WRAPPER = "localization-overlay/apply_localization_overlay_reviewed.py"
FINAL_OVERRIDE_FILES = (
    "localization-overlay/catalog-final-overrides.ja-01.json",
    "localization-overlay/catalog-final-overrides.ja-02.json",
    "localization-overlay/catalog-final-overrides.pt-01.json",
    "localization-overlay/catalog-final-overrides.zh-01.json",
    "localization-overlay/catalog-final-overrides.zh-02.json",
)


def load_wrapper():
    spec = importlib.util.spec_from_file_location("hostpanel_localization_reviewed", WRAPPER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load reviewed localization wrapper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LocalizationBootstrapWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bootstrap = BOOTSTRAP.read_text(encoding="utf-8")
        cls.localization_workflow = LOCALIZATION_WORKFLOW.read_text(encoding="utf-8")
        cls.qemu_workflow = QEMU_WORKFLOW.read_text(encoding="utf-8")
        cls.wrapper = load_wrapper()

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
        self.assertIn("test_localization_bootstrap_wiring.py", self.localization_workflow)
        self.assertIn("apply_localization_overlay_reviewed.py", self.localization_workflow)

    def test_qemu_runs_for_localization_changes(self):
        self.assertIn("      - localization-overlay/**", self.qemu_workflow)
        self.assertIn("      - tests/test_high_risk_locale_overrides.py", self.qemu_workflow)
        self.assertIn("      - tests/test_localization_bootstrap_wiring.py", self.qemu_workflow)
        self.assertIn("test_high_risk_locale_overrides.py", self.qemu_workflow)
        self.assertIn("test_localization_bootstrap_wiring.py", self.qemu_workflow)

    def test_runtime_digest_constants_match_reviewed_layers(self):
        self.assertEqual(self.wrapper.EXPECTED_BASE_COUNTS, {"ja": 19, "pt": 21, "zh": 15})
        self.assertEqual(
            self.wrapper.EXPECTED_BASE_CANONICAL_SHA256,
            "98e88a7c679eb3b4342a268deac8b0548c4e9509a1769b3ffc5626411a388604",
        )
        self.assertEqual(self.wrapper.EXPECTED_FINAL_COUNTS, {"ja": 91, "pt": 31, "zh": 60})
        self.assertEqual(
            self.wrapper.EXPECTED_FINAL_CANONICAL_SHA256,
            "5bbb02dfacb69ed83157a89348ac2e24da85665ffed8b6eb1866ca69ad232b5f",
        )
        self.assertEqual(self.wrapper.EXPECTED_VISIBLE_COUNTS, {"ja": 110, "pt": 52, "zh": 75})
        self.assertEqual(
            self.wrapper.EXPECTED_VISIBLE_CANONICAL_SHA256,
            "6d17c244c021aa08edc4a0a14cb7c49427e9bb5653e7e36725efa82a8fc0afec",
        )

    def test_runtime_payload_validator_accepts_only_exact_reviewed_data(self):
        valid = {"ja": {"key": "値"}, "pt": {}, "zh": {}}
        digest = self.wrapper.canonical_sha256(valid)
        self.wrapper.validate_reviewed_payload(
            "test payload", valid, {"ja": 1, "pt": 0, "zh": 0}, digest
        )

        invalid_cases = (
            ({"ja": {"key": "値"}}, {"ja": 1, "pt": 0, "zh": 0}, digest),
            ({"ja": {"key": ""}, "pt": {}, "zh": {}}, {"ja": 1, "pt": 0, "zh": 0}, digest),
            (valid, {"ja": 2, "pt": 0, "zh": 0}, digest),
            (valid, {"ja": 1, "pt": 0, "zh": 0}, "0" * 64),
        )
        for payload, counts, expected_digest in invalid_cases:
            with self.subTest(payload=payload, counts=counts, digest=expected_digest):
                with self.assertRaises(SystemExit):
                    self.wrapper.validate_reviewed_payload(
                        "test payload", payload, counts, expected_digest
                    )


if __name__ == "__main__":
    unittest.main()
