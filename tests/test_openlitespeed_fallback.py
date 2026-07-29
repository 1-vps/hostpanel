import pathlib
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
HARDENER = ROOT / "tools" / "harden_install.py"
BASE = ROOT / "install.base.sh"


class OpenLiteSpeedFallbackTests(unittest.TestCase):
    def test_generated_installer_honors_optional_backend_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            generated = pathlib.Path(directory) / "install.generated.sh"
            subprocess.run(
                [sys.executable, str(HARDENER), str(BASE), str(generated)],
                cwd=ROOT,
                check=True,
            )
            subprocess.run(["bash", "-n", str(generated)], check=True)
            text = generated.read_text(encoding="utf-8")

        self.assertNotIn(
            'die "OpenLiteSpeed binary is missing after installation"',
            text,
        )
        self.assertIn(
            'warn "OpenLiteSpeed backend unavailable; nginx and Apache remain active"',
            text,
        )
        self.assertIn(
            'die "OpenLiteSpeed rejected the HostPanel configuration"',
            text,
        )
        self.assertIn('die "OpenLiteSpeed failed to start"', text)

    def test_hardener_fails_closed_on_reviewed_fallback_shape(self):
        hardener = HARDENER.read_text(encoding="utf-8")
        self.assertIn("OpenLiteSpeed optional fallback", hardener)
        self.assertIn("_replace_once", hardener)


if __name__ == "__main__":
    unittest.main()
