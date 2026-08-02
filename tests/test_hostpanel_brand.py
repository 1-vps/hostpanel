from __future__ import annotations

import pathlib
import re
import unittest
import xml.etree.ElementTree as ET


ROOT = pathlib.Path(__file__).resolve().parents[1]
MARK = ROOT / "app" / "static" / "hostpanel-mark.svg"
LOGO = ROOT / "app" / "static" / "hostpanel-logo.svg"
BRAND_CSS = ROOT / "app" / "static" / "hostpanel-brand.css"
PATCHER = ROOT / "tools" / "patch_panel_ui.py"
BOOTSTRAP = ROOT / "bootstrap-install.sh"
README = ROOT / "README.md"
UI_WORKFLOW = ROOT / ".github" / "workflows" / "ui-audit.yml"
QEMU_WORKFLOW = ROOT / ".github" / "workflows" / "qemu-vm-acceptance.yml"


class HostPanelBrandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mark = MARK.read_text(encoding="utf-8")
        cls.logo = LOGO.read_text(encoding="utf-8")
        cls.css = BRAND_CSS.read_text(encoding="utf-8")
        cls.patcher = PATCHER.read_text(encoding="utf-8")
        cls.bootstrap = BOOTSTRAP.read_text(encoding="utf-8")
        cls.readme = README.read_text(encoding="utf-8")
        cls.ui_workflow = UI_WORKFLOW.read_text(encoding="utf-8")
        cls.qemu_workflow = QEMU_WORKFLOW.read_text(encoding="utf-8")

    def test_svg_assets_are_self_contained_and_safe(self) -> None:
        for path, source in ((MARK, self.mark), (LOGO, self.logo)):
            with self.subTest(path=path.name):
                root = ET.fromstring(source)
                self.assertEqual(root.tag, "{http://www.w3.org/2000/svg}svg")
                self.assertRegex(root.attrib.get("viewBox", ""), r"^0 0 [0-9]+ [0-9]+$")
                lowered = source.lower()
                for forbidden in ("<script", "<foreignobject", "javascript:", "data:text/html"):
                    self.assertNotIn(forbidden, lowered)
                self.assertNotRegex(source, r"(?:href|src)=[\"']https?://")
        self.assertIn(">Host<", self.logo)
        self.assertIn(">Panel<", self.logo)

    def test_brand_layer_uses_the_mark_without_remote_resources(self) -> None:
        self.assertIn("url('/static/hostpanel-mark.svg')", self.css)
        self.assertIn(".brand-mark::before{content:none}", self.css)
        self.assertNotRegex(self.css, r"url\(\s*[\"']?https?://")
        self.assertNotIn("@import", self.css)

    def test_panel_patcher_loads_brand_style_and_favicon(self) -> None:
        self.assertIn("/static/hostpanel-brand.css", self.patcher)
        self.assertIn("/static/hostpanel-mark.svg", self.patcher)
        self.assertIn("brand stylesheet", self.patcher)
        self.assertIn("brand favicon", self.patcher)

    def test_bootstrap_blob_verifies_and_installs_brand_assets(self) -> None:
        for path in (
            "app/static/hostpanel-brand.css",
            "app/static/hostpanel-mark.svg",
            "app/static/hostpanel-logo.svg",
        ):
            self.assertIn(path, self.bootstrap)
            self.assertIn(path, self.ui_workflow)
            self.assertIn(path, self.qemu_workflow)
        self.assertIn(
            'install -m 0644 "$CHECKOUT/app/static/hostpanel-brand.css"',
            self.bootstrap,
        )
        self.assertIn(
            'install -m 0644 "$CHECKOUT/app/static/hostpanel-mark.svg"',
            self.bootstrap,
        )
        self.assertIn(
            'install -m 0644 "$CHECKOUT/app/static/hostpanel-logo.svg"',
            self.bootstrap,
        )

    def test_readme_uses_the_wordmark(self) -> None:
        self.assertIn('src="app/static/hostpanel-logo.svg"', self.readme)
        self.assertIn('alt="HostPanel"', self.readme)

    def test_workflows_run_the_brand_regression(self) -> None:
        for workflow in (self.ui_workflow, self.qemu_workflow):
            self.assertIn("tests/test_hostpanel_brand.py", workflow)
            self.assertIn("test_hostpanel_brand.py", workflow)


if __name__ == "__main__":
    unittest.main()
