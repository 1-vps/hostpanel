import pathlib
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
HARDENER = ROOT / "tools" / "harden_install.py"
BASE = ROOT / "install.base.sh"


class OpenLiteSpeedFallbackTests(unittest.TestCase):
    def test_generated_installer_defaults_to_nginx_apache(self):
        with tempfile.TemporaryDirectory() as directory:
            generated = pathlib.Path(directory) / "install.generated.sh"
            subprocess.run(
                [sys.executable, str(HARDENER), str(BASE), str(generated)],
                cwd=ROOT,
                check=True,
            )
            subprocess.run(["bash", "-n", str(generated)], check=True)
            text = generated.read_text(encoding="utf-8")

        self.assertIn(
            'WEBSERVER_MODE="${HP_WEBSERVER_MODE:-nginx_apache}"',
            text,
        )
        self.assertIn(
            'if has_role web && [[ "$WEBSERVER_MODE" == openlitespeed ]]',
            text,
        )
        self.assertIn(
            'if has_role web && [[ "$WEBSERVER_MODE" == openlitespeed ]]; then',
            text,
        )
        self.assertIn(
            'OpenLiteSpeed mode was requested but the runtime is unavailable',
            text,
        )
        self.assertIn(
            'warn "OpenLiteSpeed backend unavailable; nginx and Apache remain active"',
            text,
        )

    def test_hardener_fails_closed_on_reviewed_webserver_shapes(self):
        hardener = HARDENER.read_text(encoding="utf-8")
        self.assertIn("OpenLiteSpeed optional fallback", hardener)
        self.assertIn("CustomBuild nginx_apache default", hardener)
        self.assertIn("optional OpenLiteSpeed installation", hardener)
        self.assertIn("requested OpenLiteSpeed availability", hardener)
        self.assertIn("replace_reviewed_shape", hardener)


if __name__ == "__main__":
    unittest.main()
