from __future__ import annotations

import collections
import json
import pathlib
import re
import shutil
import subprocess
import tarfile
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PATCHER = ROOT / "tools" / "patch_panel_ui.py"
CSS = ROOT / "app" / "static" / "panel-redesign.css"
JS = ROOT / "app" / "static" / "panel-redesign.js"
BOOTSTRAP = ROOT / "bootstrap-install.sh"
QEMU_WORKFLOW = ROOT / ".github" / "workflows" / "qemu-vm-acceptance.yml"


def signed_archive() -> pathlib.Path:
    archives = sorted(ROOT.glob("hostpanel-*-source.tar.gz"))
    if len(archives) != 1:
        raise RuntimeError(f"expected exactly one signed source archive, found {len(archives)}")
    return archives[0]


def read_signed_members() -> tuple[str, dict[str, dict[str, str]]]:
    template = ""
    catalogs: dict[str, dict[str, str]] = {}
    with tarfile.open(signed_archive(), "r:gz") as archive:
        for member in archive.getmembers():
            if member.name.endswith("/app/templates/panel.html"):
                handle = archive.extractfile(member)
                if handle is None:
                    raise RuntimeError("could not read signed panel template")
                template = handle.read().decode("utf-8", errors="strict")
            match = re.search(r"/app/static/i18n\.([a-z]{2})\.json$", member.name)
            if match:
                handle = archive.extractfile(member)
                if handle is None:
                    raise RuntimeError(f"could not read {member.name}")
                catalogs[match.group(1)] = json.loads(
                    handle.read().decode("utf-8", errors="strict")
                )
    if not template:
        raise RuntimeError("signed panel template was not found")
    return template, catalogs


def read_redesign_catalogs(source: str) -> dict[str, dict[str, str]]:
    match = re.search(
        r"const REDESIGN_TRANSLATIONS=Object\.freeze\((\{.*?\})\);",
        source,
        re.DOTALL,
    )
    if match is None:
        raise RuntimeError("redesign translation catalog was not found")
    catalogs = json.loads(match.group(1))
    if not isinstance(catalogs, dict):
        raise RuntimeError("redesign translation catalog has an invalid shape")
    return catalogs


class PanelRedesignTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original, cls.catalogs = read_signed_members()
        cls.css = CSS.read_text(encoding="utf-8")
        cls.js = JS.read_text(encoding="utf-8")
        cls.bootstrap = BOOTSTRAP.read_text(encoding="utf-8")
        cls.workflow = QEMU_WORKFLOW.read_text(encoding="utf-8")

    def patched_template(self) -> str:
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "panel.html"
            path.write_text(self.original, encoding="utf-8")
            subprocess.run(["python3", str(PATCHER), str(path)], check=True)
            first = path.read_text(encoding="utf-8")
            subprocess.run(["python3", str(PATCHER), str(path)], check=True)
            second = path.read_text(encoding="utf-8")
            self.assertEqual(first, second, "UI patcher must be idempotent")
            return second

    def test_patcher_preserves_runtime_dom_contracts(self):
        panel = self.patched_template()
        for identifier in (
            "cpu",
            "cpud",
            "ram",
            "ramd",
            "disk",
            "diskd",
            "loadv",
            "svcBody",
            "mq",
            "dashboardState",
            "dashboardUpdated",
            "dashboardError",
            "dashboardRetry",
        ):
            self.assertEqual(panel.count(f'id="{identifier}"'), 1, identifier)
        self.assertIn('class="page hp-dashboard" data-view="dashboard"', panel)
        self.assertIn('href="/static/panel-redesign.css"', panel)
        self.assertIn('src="/static/panel-redesign.js"', panel)
        self.assertIn('data-ui-version="3.0.0"', panel)
        self.assertIn('<meta content="#2563EB" name="theme-color"/>', panel)

    def test_patched_template_has_unique_ids_and_valid_localization_keys(self):
        panel = self.patched_template()
        identifiers = re.findall(r'\bid="([A-Za-z][A-Za-z0-9_.:-]*)"', panel)
        duplicates = {
            name: count
            for name, count in collections.Counter(identifiers).items()
            if count > 1
        }
        self.assertEqual(duplicates, {})
        keys = set(re.findall(r'\bdata-i18n="([^"]+)"', panel))
        self.assertGreaterEqual(len(self.catalogs), 10)
        for language, catalog in self.catalogs.items():
            self.assertEqual(keys - set(catalog), set(), language)

    def test_redesign_translations_match_languages_keys_and_placeholders(self):
        catalogs = read_redesign_catalogs(self.js)
        self.assertEqual(set(catalogs), set(self.catalogs))
        expected_keys = set(catalogs["en"])
        placeholder = re.compile(r"\{([A-Za-z][A-Za-z0-9_]*)\}")
        for language, messages in catalogs.items():
            self.assertEqual(set(messages), expected_keys, language)
            for key, message in messages.items():
                self.assertIsInstance(message, str, f"{language}:{key}")
                self.assertTrue(message.strip(), f"{language}:{key}")
                self.assertEqual(
                    set(placeholder.findall(message)),
                    set(placeholder.findall(catalogs["en"][key])),
                    f"{language}:{key}",
                )

        self.assertEqual(catalogs["fi"]["live"], "Reaaliajassa")
        self.assertIn("file d’attente", catalogs["fr"]["queueRequiresAttention"])
        self.assertIn("verzendwachtrij", catalogs["nl"]["deliveryQueueHealthy"])
        self.assertTrue(catalogs["pl"]["runningCount"].startswith("Aktywne:"))
        self.assertIn("E-postkön", catalogs["sv"]["deliveryQueueHealthy"])

    def test_dashboard_is_accessible_and_role_aware(self):
        panel = self.patched_template()
        dashboard = panel.split('class="page hp-dashboard"', 1)[1].split('<template data-page-template="files">', 1)[0]
        self.assertIn('aria-live="polite"', dashboard)
        self.assertIn('role="status"', dashboard)
        self.assertIn('aria-labelledby="hpHealthTitle"', dashboard)
        self.assertIn('aria-labelledby="hpActionsTitle"', dashboard)
        self.assertIn('role="region" tabindex="0"', dashboard)
        self.assertNotRegex(dashboard, r"\sstyle=")
        self.assertNotRegex(dashboard, r"\son(?:click|change|input|submit|keydown)=")
        self.assertIn("function syncPageLinkVisibility()", self.js)
        self.assertIn("control.hidden=!allowed", self.js)
        self.assertIn("if(!page||!pageLinkAllowed(navLink))", self.js)
        self.assertIn("const schedulePageLinkVisibility=scheduleFrame", self.js)
        self.assertIn("attributeFilter:['hidden','aria-hidden']", self.js)
        self.assertNotIn("attributeFilter:['hidden','aria-hidden','class','style']", self.js)

    def test_visual_system_covers_responsive_dark_and_accessibility_modes(self):
        for token in (
            "--hp-blue",
            "--hp-navy",
            "--hp-panel",
            "--hp-shadow-sm",
            ".hp-dashboard-layout",
            ".hp-metric-grid",
            ".hp-health-ring",
            ".hp-quick-grid",
            ".hp-dashboard-rail",
        ):
            self.assertIn(token, self.css)
        for query in (
            "@media(max-width:1240px)",
            "@media(max-width:1060px)",
            "@media(max-width:900px)",
            "@media(max-width:680px)",
            "@media(max-width:360px)",
            "@media(prefers-reduced-motion:reduce)",
            "@media(prefers-contrast:more)",
            "@media(forced-colors:active)",
        ):
            self.assertIn(query, self.css)
        self.assertIn("body.hp-redesign.dark", self.css)
        self.assertIn("left:calc(-1 * min(280px,88vw))", self.css)
        self.assertIn("min-width:44px;min-height:44px", self.css)
        self.assertIn("height:100dvh", self.css)
        self.assertIn("env(safe-area-inset-top)", self.css)
        self.assertIn("env(safe-area-inset-bottom)", self.css)
        self.assertIn("overflow-wrap:anywhere", self.css)
        self.assertIn("animation-duration:.01ms!important", self.css)
        self.assertIn("backdrop-filter:none", self.css)
        self.assertNotIn("@import", self.css)
        self.assertNotRegex(self.css, r"url\(\s*['\"]?https?://")

    def test_redesign_script_is_bounded_and_does_not_bypass_panel_api(self):
        for unsafe in (
            "eval(",
            "new Function",
            "document.write",
            ".innerHTML",
            "fetch(",
            "XMLHttpRequest",
            "localStorage",
            "sessionStorage",
            "setInterval(",
        ):
            self.assertNotIn(unsafe, self.js)
        self.assertIn("MutationObserver", self.js)
        self.assertIn("CSS.escape(page)", self.js)
        self.assertIn("location.hash=`#/panel/${page}`", self.js)
        self.assertIn("document.body.dataset.uiVersion='3.0.0'", self.js)
        self.assertIn("const scheduleFrame=callback=>", self.js)
        self.assertIn("(?:[.,]\\d+)?", self.js)
        self.assertIn("normalizeLanguage(document.documentElement.lang)", self.js)
        self.assertIn("mirrorText('uptime',['dashboardUptime','dashboardUptimeRail'])", self.js)
        self.assertIn("dataset.hpRedesignBound", self.js)

    def test_patcher_fails_closed_for_unsafe_or_unreviewed_targets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            target = root / "panel.html"
            target.write_text("<html></html>", encoding="utf-8")
            result = subprocess.run(
                ["python3", str(PATCHER), str(target)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertNotEqual(result.returncode, 0)
            real = root / "real.html"
            real.write_text(self.original, encoding="utf-8")
            link = root / "link.html"
            link.symlink_to(real)
            result = subprocess.run(
                ["python3", str(PATCHER), str(link)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unsafe panel template target", result.stderr)

    def test_bootstrap_blob_verifies_and_installs_every_ui_overlay(self):
        for path in (
            "tools/patch_panel_ui.py",
            "app/static/panel-redesign.css",
            "app/static/panel-redesign.js",
        ):
            self.assertIn(path, self.bootstrap)
        self.assertIn(
            'install -m 0755 "$CHECKOUT/tools/patch_panel_ui.py"',
            self.bootstrap,
        )
        self.assertIn(
            'install -m 0644 "$CHECKOUT/app/static/panel-redesign.css"',
            self.bootstrap,
        )
        self.assertIn(
            'install -m 0644 "$CHECKOUT/app/static/panel-redesign.js"',
            self.bootstrap,
        )
        self.assertIn(
            'python3 "$SOURCE_ROOT/tools/patch_panel_ui.py" "$SOURCE_ROOT/app/templates/panel.html"',
            self.bootstrap,
        )
        self.assertLess(
            self.bootstrap.index("for overlay in"),
            self.bootstrap.index('python3 "$SOURCE_ROOT/tools/patch_panel_ui.py"'),
        )

    def test_qemu_acceptance_tracks_and_validates_the_ui_overlay(self):
        for path in (
            "tools/patch_panel_ui.py",
            "app/static/panel-redesign.css",
            "app/static/panel-redesign.js",
            "tests/test_panel_redesign.py",
        ):
            self.assertIn(f"      - {path}", self.workflow)
        self.assertIn("test_panel_redesign.py", self.workflow)
        browser = (ROOT / "tests" / "browser" / "panel-redesign.spec.js").read_text(encoding="utf-8")
        self.assertIn("width: 320, height: 568", browser)
        self.assertIn("width: 390, height: 844", browser)
        self.assertIn("width: 844, height: 390", browser)
        self.assertIn("width: 1440, height: 900", browser)
        self.assertIn("phone-dashboard.png", browser)
        self.assertIn("desktop-dashboard.png", browser)


if __name__ == "__main__":
    unittest.main()
