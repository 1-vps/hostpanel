import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "install.sh"


class RuntimePermissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.launcher = LAUNCHER.read_text(encoding="utf-8")
        marker_at = cls.launcher.index("<<'PYVENVRUNTIME'")
        body_at = cls.launcher.index("import os\n", marker_at)
        body_end = cls.launcher.index("\nPYVENVRUNTIME", body_at)
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

    def test_root_owned_runtime_remains_readable_and_executable(self):
        fixture = '''chown -R root:root "$PANEL_DIR/app" "$PANEL_DIR/ops" "$PANEL_DIR/venvs" "$PANEL_DIR/plugins"
find "$PANEL_DIR/app" -type d -exec chmod 755 {} +
'''
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory) / "installer.sh"
            target.write_text(fixture, encoding="utf-8")
            target.chmod(0o700)
            result = self.apply_patch(target)
            self.assertEqual(result.returncode, 0, result.stdout)
            transformed = target.read_text(encoding="utf-8")

            ownership_at = transformed.index('chown -R root:root "$PANEL_DIR/app"')
            access_at = transformed.index('chmod -R a+rX "$PANEL_DIR/venvs"')
            app_modes_at = transformed.index('find "$PANEL_DIR/app" -type d')
            self.assertLess(ownership_at, access_at)
            self.assertLess(access_at, app_modes_at)
            self.assertNotIn('chmod -R 777', transformed)
            self.assertNotIn('chown -R "$PANEL_USER', transformed)

            second = self.apply_patch(target)
            self.assertEqual(second.returncode, 0, second.stdout)
            self.assertEqual(target.read_text(encoding="utf-8"), transformed)

    def test_patch_rejects_an_unreviewed_installer_shape(self):
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory) / "installer.sh"
            target.write_text("echo unrelated\n", encoding="utf-8")
            result = self.apply_patch(target)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("unexpected runtime permission shape", result.stdout)


if __name__ == "__main__":
    unittest.main()
