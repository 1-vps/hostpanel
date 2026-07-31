#!/usr/bin/env python3
from __future__ import annotations

import json
import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "app" / "static" / "panel-redesign.js"
FUTURE_LOCALES = {"ja", "pt", "zh"}


def read_catalog(source: str, name: str) -> dict[str, dict[str, str]]:
    match = re.search(
        rf"const {re.escape(name)}=Object\.freeze\((\{{.*?\}})\);",
        source,
        re.DOTALL,
    )
    if match is None:
        raise RuntimeError(f"{name} was not found")
    payload = json.loads(match.group(1))
    if not isinstance(payload, dict):
        raise RuntimeError(f"{name} has an invalid shape")
    return payload


class PanelFutureLocaleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = SCRIPT.read_text(encoding="utf-8")
        cls.production = read_catalog(cls.source, "REDESIGN_TRANSLATIONS")
        cls.future = read_catalog(cls.source, "FUTURE_REDESIGN_TRANSLATIONS")

    def test_future_locales_are_explicit_and_disjoint(self) -> None:
        self.assertEqual(set(self.future), FUTURE_LOCALES)
        self.assertEqual(set(self.production) & set(self.future), set())
        self.assertIn("...REDESIGN_TRANSLATIONS", self.source)
        self.assertIn("...FUTURE_REDESIGN_TRANSLATIONS", self.source)
        self.assertIn("Object.hasOwn(ALL_REDESIGN_TRANSLATIONS,candidate)", self.source)

    def test_future_locales_match_english_keys_and_placeholders(self) -> None:
        expected_keys = set(self.production["en"])
        placeholder = re.compile(r"\{([A-Za-z][A-Za-z0-9_]*)\}")
        for language, messages in self.future.items():
            self.assertEqual(set(messages), expected_keys, language)
            for key, message in messages.items():
                with self.subTest(language=language, key=key):
                    self.assertIsInstance(message, str)
                    self.assertTrue(message.strip())
                    self.assertEqual(
                        set(placeholder.findall(message)),
                        set(placeholder.findall(self.production["en"][key])),
                    )

    def test_representative_future_copy_is_not_english(self) -> None:
        self.assertEqual(self.future["ja"]["open"], "開く")
        self.assertEqual(self.future["pt"]["dashboardOverview"], "Visão geral do painel")
        self.assertEqual(self.future["zh"]["live"], "实时")
        for language in FUTURE_LOCALES:
            self.assertNotEqual(
                self.future[language]["refreshDashboardData"],
                self.production["en"]["refreshDashboardData"],
            )


if __name__ == "__main__":
    unittest.main()
