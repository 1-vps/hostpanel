#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
PATCHER_PATH = ROOT / "tools" / "patch_panel_ui.py"


def load_patcher():
    spec = importlib.util.spec_from_file_location("hostpanel_patch_panel_ui", PATCHER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load panel patcher")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def template(dashboard: str) -> str:
    return f'''<!doctype html>
<html>
<head>
<meta content="#0F766E" name="theme-color"/>
<link href="/static/panel.css" rel="stylesheet"/>
</head>
<body data-ui-version="2.0.0">
{dashboard}
<template data-page-template="files"><div id="files-page"></div></template>
<script defer="" src="/static/panel-i18n.js"></script>
</body>
</html>
'''


class PanelPatcherSectionBoundsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.patcher = load_patcher()

    def test_nested_sections_are_replaced_with_the_complete_dashboard(self) -> None:
        source = template('''<section class="page" data-view="dashboard">
<div id="cpu"></div><div id="ram"></div><div id="disk"></div>
<section id="nested-sentinel"><div>nested</div></section>
<div id="loadv"></div><table><tbody id="svcBody"></tbody></table>
<div id="mq"></div><div id="old-dashboard-tail">old tail</div>
</section>''')

        patched = self.patcher.patch(source)

        self.assertNotIn("nested-sentinel", patched)
        self.assertNotIn("old-dashboard-tail", patched)
        self.assertEqual(
            patched.count('class="page hp-dashboard" data-view="dashboard"'),
            1,
        )
        self.assertEqual(patched.count('id="cpu"'), 1)
        self.assertIn('id="files-page"', patched)

    def test_unbalanced_dashboard_sections_fail_closed(self) -> None:
        source = template('''<section class="page" data-view="dashboard">
<div id="cpu"></div><div id="ram"></div><div id="disk"></div>
<section id="unclosed-nested">
<div id="loadv"></div><table><tbody id="svcBody"></tbody></table>
<div id="mq"></div>
</section>''')

        with self.assertRaisesRegex(SystemExit, "section tags are unbalanced"):
            self.patcher.patch(source)


if __name__ == "__main__":
    unittest.main()
