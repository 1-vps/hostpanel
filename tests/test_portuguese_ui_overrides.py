import hashlib
import importlib.util
import json
import pathlib
import re
import tarfile
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
OVERLAY = ROOT / "localization-overlay"
UI_FILE = OVERLAY / "catalog-visible-ui-overrides.pt.json"
FINAL_FILES = (
    "catalog-final-overrides.ja-01.json",
    "catalog-final-overrides.ja-02.json",
    "catalog-final-overrides.pt-01.json",
    "catalog-final-overrides.zh-01.json",
    "catalog-final-overrides.zh-02.json",
)
EXPECTED_COUNTS = {"pt": 80}
EXPECTED_SHA256 = "193ef6c9f6b0e3b36f755ace7d685109974ae30aa480bd4db9bdc01eceb2c08c"
PLACEHOLDER = re.compile(r"\{[A-Za-z0-9_.-]+\}")


def canonical_sha256(payload: dict[str, dict[str, str]]) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_signed_english_catalog() -> dict[str, str]:
    archives = sorted(ROOT.glob("hostpanel-*-source.tar.gz"))
    if len(archives) != 1:
        raise RuntimeError(f"expected exactly one signed source archive, found {len(archives)}")
    with tarfile.open(archives[0], "r:gz") as archive:
        matches = [
            member for member in archive.getmembers()
            if member.name.endswith("/app/static/i18n.en.json")
        ]
        if len(matches) != 1:
            raise RuntimeError(f"expected exactly one English catalog, found {len(matches)}")
        handle = archive.extractfile(matches[0])
        if handle is None:
            raise RuntimeError("could not read signed English catalog")
        return json.loads(handle.read().decode("utf-8", errors="strict"))


def load_wrapper():
    path = OVERLAY / "apply_localization_overlay_reviewed.py"
    spec = importlib.util.spec_from_file_location("hostpanel_localization_reviewed", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load reviewed localization wrapper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PortugueseUiOverrideTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.wrapper = load_wrapper()
        cls.payload = json.loads(UI_FILE.read_text(encoding="utf-8"))
        cls.english = load_signed_english_catalog()

    def load_ui_files(self, overlay: pathlib.Path) -> dict[str, dict[str, str]]:
        return self.wrapper.load_review_files(
            overlay,
            self.wrapper.PORTUGUESE_UI_OVERRIDE_FILES,
            "catalog-visible-ui-overrides.*.json",
            frozenset({"pt"}),
            "Portuguese UI override",
        )

    def test_payload_is_exactly_locked(self):
        self.assertFalse(UI_FILE.is_symlink())
        self.assertEqual(
            self.wrapper.PORTUGUESE_UI_OVERRIDE_FILES,
            (UI_FILE.name,),
        )
        self.assertEqual(
            {locale: len(entries) for locale, entries in self.payload.items()},
            EXPECTED_COUNTS,
        )
        self.assertEqual(canonical_sha256(self.payload), EXPECTED_SHA256)
        self.assertEqual(self.wrapper.EXPECTED_UI_COUNTS, EXPECTED_COUNTS)
        self.assertEqual(self.wrapper.EXPECTED_UI_CANONICAL_SHA256, EXPECTED_SHA256)

    def test_keys_are_visible_ui_and_preserve_placeholders(self):
        for key, value in self.payload["pt"].items():
            with self.subTest(key=key):
                self.assertTrue(key.startswith(("dialog.", "errors.", "route.")))
                self.assertIn(key, self.english)
                self.assertNotEqual(value, self.english[key])
                self.assertEqual(
                    sorted(PLACEHOLDER.findall(value)),
                    sorted(PLACEHOLDER.findall(self.english[key])),
                )
        self.assertEqual(self.payload["pt"]["route.cpanel"], "cPanel")
        self.assertEqual(self.payload["pt"]["route.directadmin"], "DirectAdmin")

    def test_payload_does_not_overlap_existing_review_layers(self):
        reviewed = json.loads(
            (OVERLAY / "catalog-overrides.json").read_text(encoding="utf-8")
        )["pt"]
        reviewed_keys = set(reviewed)
        for file_name in FINAL_FILES:
            payload = json.loads((OVERLAY / file_name).read_text(encoding="utf-8"))
            reviewed_keys.update(payload.get("pt", {}))
        self.assertEqual(reviewed_keys & set(self.payload["pt"]), set())

    def test_runtime_loader_applies_every_ui_value(self):
        core = self.wrapper.load_core()
        self.wrapper.install_final_override_loader(core)
        overrides = json.loads(
            (OVERLAY / "catalog-overrides.json").read_text(encoding="utf-8")
        )
        core.load_override_bundle(OVERLAY, overrides)
        for key, expected in self.payload["pt"].items():
            with self.subTest(key=key):
                self.assertEqual(overrides["pt"][key], expected)

    def test_runtime_loader_rejects_missing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(SystemExit):
                self.load_ui_files(pathlib.Path(directory))

    def test_runtime_loader_rejects_extra_matching_file(self):
        with tempfile.TemporaryDirectory() as directory:
            overlay = pathlib.Path(directory)
            (overlay / UI_FILE.name).write_text(
                UI_FILE.read_text(encoding="utf-8"), encoding="utf-8"
            )
            (overlay / "catalog-visible-ui-overrides.extra.json").write_text(
                '{"pt":{"route.dashboard":"Painel"}}\n', encoding="utf-8"
            )
            with self.assertRaises(SystemExit):
                self.load_ui_files(overlay)

    def test_runtime_loader_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            overlay = pathlib.Path(directory)
            target = overlay / "payload.json"
            target.write_text(UI_FILE.read_text(encoding="utf-8"), encoding="utf-8")
            try:
                (overlay / UI_FILE.name).symlink_to(target.name)
            except (NotImplementedError, OSError) as exc:
                self.skipTest(f"symlinks are unavailable: {exc}")
            with self.assertRaises(SystemExit):
                self.load_ui_files(overlay)

    def test_runtime_loader_rejects_wrong_locale(self):
        with tempfile.TemporaryDirectory() as directory:
            overlay = pathlib.Path(directory)
            (overlay / UI_FILE.name).write_text(
                '{"es":{"route.dashboard":"Panel"}}\n', encoding="utf-8"
            )
            with self.assertRaises(SystemExit):
                self.load_ui_files(overlay)

    def test_runtime_validator_rejects_digest_drift(self):
        changed = json.loads(json.dumps(self.payload, ensure_ascii=False))
        changed["pt"]["route.dashboard"] = "Painel alterado"
        with self.assertRaises(SystemExit):
            self.wrapper.validate_reviewed_payload(
                "Portuguese UI override",
                changed,
                EXPECTED_COUNTS,
                EXPECTED_SHA256,
            )


if __name__ == "__main__":
    unittest.main()
