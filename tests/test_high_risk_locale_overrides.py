import hashlib
import json
import pathlib
import re
import tarfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
OVERRIDES = ROOT / "localization-overlay" / "catalog-overrides.json"
EXPECTED_COUNTS = {"ja": 19, "pt": 6, "zh": 15}
EXPECTED_CANONICAL_SHA256 = "291c23ad5b9f85fd7662cd9906985aab0bd33e3a961be8a69b6d946ef70e27f1"
PLACEHOLDER = re.compile(r"\{[A-Za-z0-9_.-]+\}")


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


if __name__ == "__main__":
    unittest.main()
