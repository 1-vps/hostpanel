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
    prefix = f"<<'{marker}' " + chr(92) + "\n"
    self_contained = text.split(prefix, 1)[1]
    self_contained = self_contained.split("\n", 1)[1]
    return self_contained.split(f"\n{marker}\n", 1)[0]


class StableRuntimeEntrypointTests(unittest.TestCase):
    def test_generated_installer_uses_stable_service_and_console_entrypoints(self):
        with tempfile.TemporaryDirectory() as directory:
            generated = pathlib.Path(directory) / "install.generated.sh"
            subprocess.run(
                [sys.executable, str(HARDENER), str(BASE), str(generated)],
                cwd=ROOT,
                check=True,
            )
            patch = launcher_patch("PYUVICORNEXEC")
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
            self.assertIn(
                "ExecStart=$PANEL_DIR/venv/bin/python -m uvicorn main:app",
                transformed,
            )
            self.assertIn('cat >"$RUNTIME_DIR/bin/uvicorn" <<EOFUVICORNWRAPPER', transformed)
            self.assertIn(
                'exec "$PANEL_DIR/venv/bin/python" -m uvicorn "\\$@"',
                transformed,
            )
            self.assertIn('chmod 755 "$RUNTIME_DIR/bin/uvicorn"', transformed)
            wrapper = transformed.index("EOFUVICORNWRAPPER")
            service = transformed.index('say "Registering the systemd service"')
            edge = transformed.index('say "Enabling the anti-DDoS edge"')
            self.assertLess(wrapper, service)
            self.assertLess(service, edge)
            subprocess.run(["bash", "-n", str(generated)], check=True)

    def test_runtime_fix_is_fail_closed_and_runs_before_installer(self):
        launcher = LAUNCHER.read_text(encoding="utf-8")
        self.assertEqual(launcher.count("<<'PYUVICORNEXEC'"), 1)
        self.assertIn("unexpected {label} shape", launcher)
        self.assertIn("Uvicorn service execution", launcher)
        self.assertIn("relocatable Uvicorn wrapper", launcher)
        patch = launcher.index("PYUVICORNEXEC")
        syntax = launcher.index('bash -n "$GENERATED_INSTALLER"')
        execute = launcher.index('bash "$GENERATED_INSTALLER" "$@"')
        self.assertLess(patch, syntax)
        self.assertLess(syntax, execute)


if __name__ == "__main__":
    unittest.main()
