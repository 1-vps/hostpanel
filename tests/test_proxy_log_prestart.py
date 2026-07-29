import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "install.sh"


class ProxyLogPrestartTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.launcher = LAUNCHER.read_text(encoding="utf-8")
        marker = "<<'PYPROXYLOG'"
        marker_at = cls.launcher.index(marker)
        body_at = cls.launcher.index("import os\n", marker_at)
        body_end = cls.launcher.index("\nPYPROXYLOG", body_at)
        cls.patch = cls.launcher[body_at:body_end]

    def apply_patch(self, target: pathlib.Path):
        return subprocess.run(
            ["python3", "-", str(target)],
            input=self.patch,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )

    def test_proxy_log_is_prepared_before_first_service_start(self):
        fixture = '''systemctl daemon-reload
mkdir -p /var/lib/hostpanel /var/lib/hostpanel/migrations /var/lib/hostpanel/root-work
chown "$PANEL_USER:$PANEL_USER" /var/lib/hostpanel /var/lib/hostpanel/migrations
chown root:root /var/lib/hostpanel/root-work
chmod 700 /var/lib/hostpanel/migrations /var/lib/hostpanel/root-work
systemctl enable --now hostpanel >>"$LOG" 2>&1
'''
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory) / "installer.sh"
            target.write_text(fixture, encoding="utf-8")
            target.chmod(0o700)
            result = self.apply_patch(target)
            self.assertEqual(result.returncode, 0, result.stdout)
            transformed = target.read_text(encoding="utf-8")

            create_at = transformed.index(
                'install -o "$PANEL_USER" -g "$PANEL_USER" -m 640 /dev/null "$PROXY_TRAFFIC_LOG"'
            )
            start_at = transformed.index("systemctl enable --now hostpanel")
            self.assertLess(create_at, start_at)
            self.assertIn('! -L "$PROXY_TRAFFIC_LOG"', transformed)
            self.assertIn('chmod 640 "$PROXY_TRAFFIC_LOG"', transformed)

            second = self.apply_patch(target)
            self.assertEqual(second.returncode, 0, second.stdout)
            self.assertEqual(target.read_text(encoding="utf-8"), transformed)

    def test_patch_rejects_an_unreviewed_installer_shape(self):
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory) / "installer.sh"
            target.write_text("echo unrelated\n", encoding="utf-8")
            result = self.apply_patch(target)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("unexpected proxy traffic log pre-start shape", result.stdout)


if __name__ == "__main__":
    unittest.main()
