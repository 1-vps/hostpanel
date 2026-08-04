from __future__ import annotations

import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
JS = ROOT / 'app/static/panel-redesign.js'


class PanelLiveRegionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = JS.read_text(encoding='utf-8')

    def test_interactive_dashboard_group_is_removed_from_live_region(self):
        self.assertIn(
            "const group=byId('dashboardState');", self.source
        )
        self.assertIn("group.removeAttribute('role');", self.source)
        self.assertIn("group.removeAttribute('aria-live');", self.source)
        self.assertIn("group.removeAttribute('aria-atomic');", self.source)

    def test_update_and_error_channels_are_separate_and_atomic(self):
        self.assertIn(
            "const updated=byId('dashboardUpdated');", self.source
        )
        self.assertIn(
            "updated.setAttribute('role','status');", self.source
        )
        self.assertIn(
            "updated.setAttribute('aria-live','polite');", self.source
        )
        self.assertIn(
            "updated.setAttribute('aria-atomic','true');", self.source
        )
        self.assertIn("const error=byId('dashboardError');", self.source)
        self.assertIn("error.setAttribute('role','alert');", self.source)
        self.assertIn(
            "error.setAttribute('aria-live','assertive');", self.source
        )
        self.assertIn(
            "error.setAttribute('aria-atomic','true');", self.source
        )

    def test_announcement_hardening_runs_before_dashboard_bindings(self):
        boot = self.source.split('function boot(){', 1)[1]
        self.assertLess(
            boot.index('hardenDashboardAnnouncements();'),
            boot.index("setMeter('cpu','cpuMeter');"),
        )
        self.assertEqual(
            self.source.count('hardenDashboardAnnouncements();'), 1
        )


if __name__ == '__main__':
    unittest.main()
