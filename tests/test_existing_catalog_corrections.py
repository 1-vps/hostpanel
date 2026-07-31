#!/usr/bin/env python3
from __future__ import annotations

import json
import pathlib
import re
import subprocess
import tarfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
OVERLAY = ROOT / "localization-overlay"
EXPECTED_FILES = {
    "existing-catalog-corrections.json": "b0b6a0b6f2176ba5f6c98d3ac6d80dcca5e7c43f",
    "existing-catalog-corrections.sv-ui-1.json": "9011dfafc66d79e61e7384b44e8900d64605edc5",
    "existing-catalog-corrections.sv-ui-2.json": "a7b45ab2320169c8ad4cffd10956756cc637b2b6",
}
PRODUCTION_LOCALES = {"da", "de", "en", "es", "fi", "fr", "nb", "nl", "pl", "sv"}
PLACEHOLDER = re.compile(r"\{[A-Za-z0-9_.-]+\}")


def load_signed_english_catalog() -> dict[str, str]:
    archives = sorted(ROOT.glob("hostpanel-*-source.tar.gz"))
    if len(archives) != 1:
        raise RuntimeError(f"expected exactly one signed source archive, found {len(archives)}")
    with tarfile.open(archives[0], "r:gz") as archive:
        matches = [
            member
            for member in archive.getmembers()
            if member.name.endswith("/app/static/i18n.en.json")
        ]
        if len(matches) != 1:
            raise RuntimeError(f"expected exactly one English catalog, found {len(matches)}")
        handle = archive.extractfile(matches[0])
        if handle is None:
            raise RuntimeError("could not read signed English catalog")
        return json.loads(handle.read().decode("utf-8", errors="strict"))


def git_blob(path: pathlib.Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(ROOT), "hash-object", "--", str(path)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return completed.stdout.strip()


class ExistingCatalogCorrectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.english = load_signed_english_catalog()
        cls.payloads = {
            name: json.loads((OVERLAY / name).read_text(encoding="utf-8"))
            for name in EXPECTED_FILES
        }

    def test_file_layout_and_git_blobs_are_locked(self) -> None:
        discovered = {path.name for path in OVERLAY.glob("existing-catalog-corrections*.json")}
        self.assertEqual(discovered, set(EXPECTED_FILES))
        for name, expected_blob in EXPECTED_FILES.items():
            path = OVERLAY / name
            with self.subTest(file=name):
                self.assertTrue(path.is_file())
                self.assertFalse(path.is_symlink())
                self.assertEqual(git_blob(path), expected_blob)

    def test_corrections_target_only_production_locales(self) -> None:
        for name, payload in self.payloads.items():
            with self.subTest(file=name):
                self.assertIsInstance(payload, dict)
                self.assertTrue(set(payload).issubset(PRODUCTION_LOCALES))
                self.assertNotEqual(payload, {})
        self.assertEqual(set(self.payloads["existing-catalog-corrections.sv-ui-1.json"]), {"sv"})
        self.assertEqual(set(self.payloads["existing-catalog-corrections.sv-ui-2.json"]), {"sv"})

    def test_layers_do_not_duplicate_keys(self) -> None:
        seen: dict[str, set[str]] = {}
        for name in EXPECTED_FILES:
            for locale, entries in self.payloads[name].items():
                duplicate = seen.setdefault(locale, set()) & set(entries)
                self.assertEqual(duplicate, set(), msg=f"{name}: duplicate keys for {locale}")
                seen[locale].update(entries)

    def test_all_values_are_known_nonempty_and_preserve_placeholders(self) -> None:
        for name, payload in self.payloads.items():
            for locale, entries in payload.items():
                self.assertIsInstance(entries, dict)
                for key, value in entries.items():
                    with self.subTest(file=name, locale=locale, key=key):
                        self.assertIn(key, self.english)
                        self.assertIsInstance(value, str)
                        self.assertTrue(value.strip())
                        self.assertEqual(
                            sorted(PLACEHOLDER.findall(value)),
                            sorted(PLACEHOLDER.findall(self.english[key])),
                        )


if __name__ == "__main__":
    unittest.main()
