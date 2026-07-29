import pathlib
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "install.sh"
HARDENER = ROOT / "tools" / "harden_install.py"
BASE = ROOT / "install.base.sh"


def launcher_patch(marker: str) -> str:
    text = LAUNCHER.read_text(encoding="utf-8")
    prefix = f"<<'{marker}' \\\n"
    self_contained = text.split(prefix, 1)[1]
    self_contained = self_contained.split("\n", 1)[1]
    return self_contained.split(f"\n{marker}\n", 1)[0]


class ReadinessHostHeaderTests(unittest.TestCase):
    def test_generated_probe_uses_trusted_host_over_loopback(self):
        with tempfile.TemporaryDirectory() as directory:
            generated = pathlib.Path(directory) / "install.generated.sh"
            subprocess.run(
                [sys.executable, str(HARDENER), str(BASE), str(generated)],
                cwd=ROOT,
                check=True,
            )
            original = generated.read_text(encoding="utf-8")
            self.assertIn(
                '-H "x-hostpanel-readiness: $READINESS_TOKEN"',
                original,
            )
            self.assertNotIn('-H "Host: $PANEL_HOST"', original)

            patch = launcher_patch("PYREADINESSHOST")
            for _ in range(2):
                result = subprocess.run(
                    [sys.executable, "-", str(generated)],
                    input=patch,
                    cwd=ROOT,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stdout)

            transformed = generated.read_text(encoding="utf-8")
            host = transformed.index('-H "Host: $PANEL_HOST"')
            token = transformed.index('-H "x-hostpanel-readiness: $READINESS_TOKEN"')
            endpoint = transformed.index(
                '"http://127.0.0.1:$PANEL_BACKEND_PORT/api/internal/readiness"'
            )
            self.assertLess(host, token)
            self.assertLess(token, endpoint)
            self.assertEqual(transformed.count('-H "Host: $PANEL_HOST"'), 1)
            subprocess.run(["bash", "-n", str(generated)], check=True)

    def test_launcher_patch_is_fail_closed_and_atomic(self):
        text = LAUNCHER.read_text(encoding="utf-8")
        self.assertEqual(text.count("PYREADINESSHOST"), 2)
        self.assertIn("unexpected readiness Host header shape", text)
        self.assertIn(".readiness-host.", text)
        self.assertIn("os.replace(temporary, path)", text)


if __name__ == "__main__":
    unittest.main()
