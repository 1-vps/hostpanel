import hashlib
import importlib.util
import json
import pathlib
import re
import tarfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
OVERLAY = ROOT / "localization-overlay"
OVERRIDES = OVERLAY / "catalog-overrides.json"
EXPECTED_COUNTS = {"ja": 19, "pt": 21, "zh": 15}
EXPECTED_CANONICAL_SHA256 = "98e88a7c679eb3b4342a268deac8b0548c4e9509a1769b3ffc5626411a388604"
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


def load_complete_overrides() -> dict[str, dict[str, str]]:
    module_path = OVERLAY / "apply_localization_overlay.py"
    spec = importlib.util.spec_from_file_location("hostpanel_localization_overlay", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load localization overlay module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    overrides = json.loads(OVERRIDES.read_text(encoding="utf-8"))
    module.load_override_bundle(OVERLAY, overrides)
    return overrides


class HighRiskLocaleOverrideTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload = json.loads(OVERRIDES.read_text(encoding="utf-8"))
        cls.english = load_signed_english_catalog()

    def test_reviewed_payload_has_exact_locales_counts_and_values(self):
        self.assertEqual(
            {locale: len(entries) for locale, entries in self.payload.items()},
            EXPECTED_COUNTS,
        )
        canonical = json.dumps(
            self.payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        self.assertEqual(hashlib.sha256(canonical).hexdigest(), EXPECTED_CANONICAL_SHA256)

    def test_every_reviewed_key_exists_and_preserves_placeholders(self):
        for locale, entries in self.payload.items():
            for key, value in entries.items():
                with self.subTest(locale=locale, key=key):
                    self.assertIn(key, self.english)
                    self.assertTrue(value.strip())
                    self.assertNotEqual(value, self.english[key])
                    self.assertEqual(
                        sorted(PLACEHOLDER.findall(value)),
                        sorted(PLACEHOLDER.findall(self.english[key])),
                    )

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
