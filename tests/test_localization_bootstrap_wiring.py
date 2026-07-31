import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / "bootstrap-install.sh"
LOCALIZATION_WORKFLOW = ROOT / ".github" / "workflows" / "localization-overlay.yml"
QEMU_WORKFLOW = ROOT / ".github" / "workflows" / "qemu-vm-acceptance.yml"
CORE_PATH = ROOT / "localization-overlay" / "apply_localization_overlay.py"
WRAPPER_PATH = ROOT / "localization-overlay" / "apply_localization_overlay_reviewed.py"
WRAPPER = "localization-overlay/apply_localization_overlay_reviewed.py"
PORTUGUESE_UI_FILE = "localization-overlay/catalog-visible-ui-overrides.pt.json"
FINAL_OVERRIDE_FILES = (
    "localization-overlay/catalog-final-overrides.ja-01.json",
    "localization-overlay/catalog-final-overrides.ja-02.json",
    "localization-overlay/catalog-final-overrides.pt-01.json",
    "localization-overlay/catalog-final-overrides.zh-01.json",
    "localization-overlay/catalog-final-overrides.zh-02.json",
)


def load_wrapper():
    spec = importlib.util.spec_from_file_location(
        "hostpanel_localization_reviewed", WRAPPER_PATH
    )
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
        cls.core_text = CORE_PATH.read_text(encoding="utf-8")
        cls.wrapper_text = WRAPPER_PATH.read_text(encoding="utf-8")
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

    def test_reviewed_files_are_git_object_and_digest_authenticated(self):
        self.assertEqual(
            self.wrapper.PORTUGUESE_UI_OVERRIDE_FILES,
            (pathlib.Path(PORTUGUESE_UI_FILE).name,),
        )
        self.assertIn("catalog-visible-ui-overrides.*.json", self.wrapper_text)
        self.assertIn("def verify_checkout_git_objects(", self.wrapper_text)
        self.assertIn(
            "FINAL_OVERRIDE_FILES + PORTUGUESE_UI_OVERRIDE_FILES",
            self.wrapper_text,
        )
        self.assertIn('git_output(repository, "hash-object"', self.wrapper_text)
        self.assertEqual(self.wrapper.EXPECTED_UI_COUNTS, {"pt": 80})
        self.assertEqual(
            self.wrapper.EXPECTED_UI_CANONICAL_SHA256,
            "193ef6c9f6b0e3b36f755ace7d685109974ae30aa480bd4db9bdc01eceb2c08c",
        )

    def test_brazilian_portuguese_label_is_explicit_and_unique(self):
        core = self.wrapper.load_core()
        original_languages = list(core.LANGUAGES)
        original_by_code = dict(original_languages)

        self.wrapper.install_language_labels(core)

        self.assertEqual(len(core.LANGUAGES), len(original_languages))
        self.assertEqual(
            [code for code, _label in core.LANGUAGES].count("pt"),
            1,
        )
        self.assertEqual(
            dict(core.LANGUAGES)["pt"],
            self.wrapper.BRAZILIAN_PORTUGUESE_LABEL,
        )
        self.assertEqual(
            self.wrapper.BRAZILIAN_PORTUGUESE_LABEL,
            "Português (Brasil)",
        )
        for code, label in core.LANGUAGES:
            if code != "pt":
                with self.subTest(code=code):
                    self.assertEqual(label, original_by_code[code])
        self.assertIn(
            "install_language_labels(core)\n    install_final_override_loader(core)",
            self.wrapper_text,
        )

    def test_brazilian_portuguese_label_reaches_login_and_panel_selectors(self):
        core = self.wrapper.load_core()
        self.wrapper.install_language_labels(core)

        login_options = "\n".join(
            f'<option value="{code}">{{% if login_language == \'{code}\' %}} selected'
            f'{{% endif %}}>{label}</option>'
            for code, label in core.LANGUAGES
        )
        panel_options = "".join(
            f'<option value="{code}">{label}</option>'
            for code, label in core.LANGUAGES
        )

        expected_label = self.wrapper.BRAZILIAN_PORTUGUESE_LABEL
        self.assertIn('value="pt"', login_options)
        self.assertIn(f">{expected_label}</option>", login_options)
        self.assertIn(f'<option value="pt">{expected_label}</option>', panel_options)
        self.assertNotIn('<option value="pt">Português</option>', panel_options)
        self.assertEqual(
            self.core_text.count("for code, label in LANGUAGES"),
            2,
        )
        self.assertIn('login_options = "\\n".join(', self.core_text)
        self.assertIn('panel_options = "".join(', self.core_text)

    def test_localization_workflow_uses_wrapper_and_runs_override_regressions(self):
        self.assertIn(
            "python3 localization-overlay/apply_localization_overlay_reviewed.py",
            self.localization_workflow,
        )
        self.assertIn("test_high_risk_locale_overrides.py", self.localization_workflow)
        self.assertIn("test_portuguese_ui_overrides.py", self.localization_workflow)
        self.assertIn("test_localization_bootstrap_wiring.py", self.localization_workflow)
        self.assertIn("apply_localization_overlay_reviewed.py", self.localization_workflow)

    def test_qemu_runs_for_localization_changes(self):
        self.assertIn("      - localization-overlay/**", self.qemu_workflow)
        self.assertIn("      - tests/test_high_risk_locale_overrides.py", self.qemu_workflow)
        self.assertIn("      - tests/test_portuguese_ui_overrides.py", self.qemu_workflow)
        self.assertIn("      - tests/test_localization_bootstrap_wiring.py", self.qemu_workflow)
        self.assertIn("test_high_risk_locale_overrides.py", self.qemu_workflow)
        self.assertIn("test_portuguese_ui_overrides.py", self.qemu_workflow)
        self.assertIn("test_localization_bootstrap_wiring.py", self.qemu_workflow)

    def test_runtime_digest_constants_match_reviewed_layers(self):
        self.assertEqual(self.wrapper.EXPECTED_BASE_COUNTS, {"ja": 19, "pt": 21, "zh": 15})
        self.assertEqual(self.wrapper.EXPECTED_FINAL_COUNTS, {"ja": 91, "pt": 31, "zh": 60})
        self.assertEqual(self.wrapper.EXPECTED_VISIBLE_COUNTS, {"ja": 110, "pt": 52, "zh": 75})
        self.assertEqual(self.wrapper.EXPECTED_UI_COUNTS, {"pt": 80})

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
