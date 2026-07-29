import pathlib
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
HARDENER = ROOT / "tools" / "harden_install.py"
BASE = ROOT / "install.base.sh"


class RootActionPrerequisiteTests(unittest.TestCase):
    def test_generated_installer_creates_runtime_directories_before_chown(self):
        with tempfile.TemporaryDirectory() as directory:
            generated = pathlib.Path(directory) / "install.generated.sh"
            subprocess.run(
                [sys.executable, str(HARDENER), str(BASE), str(generated)],
                cwd=ROOT,
                check=True,
            )
            subprocess.run(["bash", "-n", str(generated)], check=True)
            text = generated.read_text(encoding="utf-8")

        stage = text.index('say "Granting the panel controlled root actions"')
        create = text.index('"$PANEL_DIR/plugins" "$PANEL_DIR/venvs"', stage)
        ownership = text.index(
            'chown -R root:root "$PANEL_DIR/app" "$PANEL_DIR/ops" "$PANEL_DIR/venvs" "$PANEL_DIR/plugins"',
            stage,
        )
        self.assertLess(create, ownership)

    def test_hardener_fails_closed_on_reviewed_directory_shape(self):
        hardener = HARDENER.read_text(encoding="utf-8")
        self.assertIn("root action runtime directories", hardener)
        self.assertIn("_replace_once", hardener)


if __name__ == "__main__":
    unittest.main()
