import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "install.sh"
BASE = ROOT / "install.base.sh"


class StableRuntimeEntrypointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.launcher = LAUNCHER.read_text(encoding="utf-8")
        cls.base = BASE.read_text(encoding="utf-8")

    def test_launcher_replaces_moved_venv_console_script(self):
        self.assertIn(
            "ExecStart=$PANEL_DIR/venv/bin/uvicorn main:app",
            self.base,
        )
        self.assertIn(
            "ExecStart=$PANEL_DIR/venv/bin/python -m uvicorn main:app",
            self.launcher,
        )
        self.assertIn("unexpected Uvicorn execution shape", self.launcher)

    def test_runtime_fix_is_applied_before_generated_installer_runs(self):
        patch = self.launcher.index("PYUVICORNEXEC")
        syntax = self.launcher.index('bash -n "$GENERATED_INSTALLER"')
        execute = self.launcher.index('bash "$GENERATED_INSTALLER" "$@"')
        self.assertLess(patch, syntax)
        self.assertLess(syntax, execute)


if __name__ == "__main__":
    unittest.main()
