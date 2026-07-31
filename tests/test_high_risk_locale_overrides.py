import hashlib
import importlib.util
import json
import pathlib
import re
import tarfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
OVERLAY = ROOT / "localization-overlay"
BASE_OVERRIDES = OVERLAY / "catalog-overrides.json"
FINAL_OVERRIDE_FILES = (
    "catalog-final-overrides.ja-01.json",
    "catalog-final-overrides.ja-02.json",
    "catalog-final-overrides.pt-01.json",
    "catalog-final-overrides.zh-01.json",
    "catalog-final-overrides.zh-02.json",
)
EXPECTED_BASE_COUNTS = {"ja": 19, "pt": 21, "zh": 15}
EXPECTED_BASE_CANONICAL_SHA256 = "98e88a7c679eb3b4342a268deac8b0548c4e9509a1769b3ffc5626411a388604"
EXPECTED_FINAL_COUNTS = {"ja": 91, "pt": 31, "zh": 60}
EXPECTED_FINAL_CANONICAL_SHA256 = "5bbb02dfacb69ed83157a89348ac2e24da85665ffed8b6eb1866ca69ad232b5f"
EXPECTED_VISIBLE_COUNTS = {"ja": 110, "pt": 52, "zh": 75}
EXPECTED_VISIBLE_CANONICAL_SHA256 = "6d17c244c021aa08edc4a0a14cb7c49427e9bb5653e7e36725efa82a8fc0afec"
PLACEHOLDER = re.compile(r"\{[A-Za-z0-9_.-]+\}")
PORTUGUESE_CONTAMINATION = re.compile(
    r"(?i)(?<![\w])(?:permanecen|hasta|contraseña|archivos|datos|correo|"
    r"seleccione|ninguna|ninguno|nodos|tabla|mensaje|mensajes|proveedor|"
    r"eliminar|eliminado|eliminada|cargando|eliminare|configurazione|"
    r"utente|utenti|nessun|aggiunto|gestire|operazioni|données|fichier|"
    r"fichiers|serveur|utilisateur|logiciel|mot de passe|sauvegarde|"
    r"supprimer|operacions|migracion|ficheiro|ficheiros|utilizador|"
    r"utilizadores|palavra-passe|equipa|ecrã|factura|facturas)(?![\w])"
)


def canonical_sha256(payload: dict[str, dict[str, str]]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


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


def load_module(path: pathlib.Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path.name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_final_overrides() -> dict[str, dict[str, str]]:
    expected = [OVERLAY / name for name in FINAL_OVERRIDE_FILES]
    discovered = sorted(OVERLAY.glob("catalog-final-overrides.*.json"))
    if discovered != sorted(expected):
        raise RuntimeError("final override file layout mismatch")
    result: dict[str, dict[str, str]] = {}
    for path in expected:
        payload = json.loads(path.read_text(encoding="utf-8"))
        for locale, entries in payload.items():
            target = result.setdefault(locale, {})
            duplicate = set(target) & set(entries)
            if duplicate:
                raise RuntimeError(f"duplicate final override keys for {locale}: {sorted(duplicate)}")
            target.update(entries)
    return result


def load_complete_overrides() -> dict[str, dict[str, str]]:
    wrapper = load_module(
        OVERLAY / "apply_localization_overlay_reviewed.py",
        "hostpanel_localization_reviewed",
    )
    core = wrapper.load_core()
    wrapper.install_final_override_loader(core)
    overrides = json.loads(BASE_OVERRIDES.read_text(encoding="utf-8"))
    core.load_override_bundle(OVERLAY, overrides)
    return overrides


class HighRiskLocaleOverrideTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = json.loads(BASE_OVERRIDES.read_text(encoding="utf-8"))
        cls.final = load_final_overrides()
        cls.english = load_signed_english_catalog()

    def test_base_reviewed_payload_is_locked(self):
        self.assertEqual(
            {locale: len(entries) for locale, entries in self.base.items()},
            EXPECTED_BASE_COUNTS,
        )
        self.assertEqual(canonical_sha256(self.base), EXPECTED_BASE_CANONICAL_SHA256)

    def test_final_semantic_payload_is_locked(self):
        self.assertEqual(
            {locale: len(entries) for locale, entries in self.final.items()},
            EXPECTED_FINAL_COUNTS,
        )
        self.assertEqual(canonical_sha256(self.final), EXPECTED_FINAL_CANONICAL_SHA256)

    def test_visible_reviewed_payload_is_locked_and_non_overlapping(self):
        visible = {locale: dict(entries) for locale, entries in self.base.items()}
        for locale, entries in self.final.items():
            overlap = set(visible.setdefault(locale, {})) & set(entries)
            self.assertEqual(overlap, set(), msg=f"{locale}: visible override overlap")
            visible[locale].update(entries)
        self.assertEqual(
            {locale: len(entries) for locale, entries in visible.items()},
            EXPECTED_VISIBLE_COUNTS,
        )
        self.assertEqual(canonical_sha256(visible), EXPECTED_VISIBLE_CANONICAL_SHA256)

    def test_every_visible_key_exists_and_preserves_placeholders(self):
        for source_name, payload in (("base", self.base), ("final", self.final)):
            for locale, entries in payload.items():
                for key, value in entries.items():
                    with self.subTest(source=source_name, locale=locale, key=key):
                        self.assertIn(key, self.english)
                        self.assertTrue(value.strip())
                        self.assertNotEqual(value, self.english[key])
                        self.assertEqual(
                            sorted(PLACEHOLDER.findall(value)),
                            sorted(PLACEHOLDER.findall(self.english[key])),
                        )

    def test_final_overrides_land_after_the_locked_bundle(self):
        complete = load_complete_overrides()
        for locale, entries in self.final.items():
            for key, expected in entries.items():
                with self.subTest(locale=locale, key=key):
                    self.assertEqual(complete[locale][key], expected)

    def test_final_portuguese_catalog_has_no_known_language_contamination(self):
        catalog = json.loads((OVERLAY / "catalogs" / "i18n.pt.json").read_text(encoding="utf-8"))
        catalog.update(load_complete_overrides()["pt"])
        contaminated = {
            key: value for key, value in catalog.items()
            if PORTUGUESE_CONTAMINATION.search(value)
        }
        self.assertEqual(contaminated, {})


if __name__ == "__main__":
    unittest.main()
