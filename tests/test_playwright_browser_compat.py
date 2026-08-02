from __future__ import annotations

import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = ROOT / "tests" / "browser" / "panel-redesign.spec.js"


class PlaywrightBrowserCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = SPEC.read_text(encoding="utf-8")

    def test_touch_target_helper_avoids_new_visible_filter_option(self) -> None:
        self.assertNotIn(".filter({ visible: true })", self.source)
        self.assertIn("const element = elements.nth(index);", self.source)
        self.assertIn("if (!(await element.isVisible())) continue;", self.source)
        self.assertIn("const box = await element.boundingBox();", self.source)
        self.assertLess(
            self.source.index("if (!(await element.isVisible())) continue;"),
            self.source.index("const box = await element.boundingBox();"),
        )


if __name__ == "__main__":
    unittest.main()
