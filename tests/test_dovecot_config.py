import pathlib
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
HARDENER = ROOT / "tools" / "harden_install.py"
BASE = ROOT / "install.base.sh"


class DovecotConfigTests(unittest.TestCase):
    def test_generated_mail_profiles_use_valid_multiline_blocks(self):
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
            "passdb { driver = passwd-file; args = /etc/dovecot/users }",
            text,
        )
        self.assertNotIn(
            "userdb { driver = passwd-file; args = /etc/dovecot/users }",
            text,
        )
        self.assertEqual(
            text.count(
                "passdb {\n  driver = passwd-file\n  args = /etc/dovecot/users\n}"
            ),
            2,
        )
        self.assertEqual(
            text.count(
                "userdb {\n  driver = passwd-file\n  args = /etc/dovecot/users\n}"
            ),
            2,
        )

    def test_hardener_fails_closed_on_both_reviewed_source_shapes(self):
        hardener = HARDENER.read_text(encoding="utf-8")
        self.assertIn('label == "Dovecot passwd-file block syntax"', hardener)
        self.assertIn("expected = 2", hardener)
        self.assertIn("count != expected", hardener)


if __name__ == "__main__":
    unittest.main()
