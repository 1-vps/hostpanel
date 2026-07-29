import pathlib
import subprocess
import sys
import tarfile
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "install.sh"
INSPECTOR = ROOT / "tools" / "inspect-release-state.py"
HARDENER = ROOT / "tools" / "harden_install.py"
BASE = ROOT / "install.base.sh"


def launcher_patch(marker: str) -> str:
    text = LAUNCHER.read_text(encoding="utf-8")
    prefix = f"<<'{marker}' \\
"
    self_contained = text.split(prefix, 1)[1]
    self_contained = self_contained.split("\n", 1)[1]
    return self_contained.split(f"\n{marker}\n", 1)[0]


def release_member(suffix: str) -> str:
    archives = sorted(ROOT.glob("hostpanel-*-source.tar.gz"))
    if len(archives) != 1:
        raise AssertionError(f"expected one source archive, found {len(archives)}")
    with tarfile.open(archives[0], "r:gz") as archive:
        members = [
            member
            for member in archive.getmembers()
            if member.isfile() and member.name.endswith(suffix)
        ]
        if len(members) != 1:
            raise AssertionError(f"expected one {suffix}, found {len(members)}")
        handle = archive.extractfile(members[0])
        if handle is None:
            raise AssertionError(f"could not extract {suffix}")
        return handle.read().decode("utf-8")


class PrivilegedManifestParserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.launcher = LAUNCHER.read_text(encoding="utf-8")
        cls.inspector = INSPECTOR.read_text(encoding="utf-8")

    def test_launcher_supports_legacy_and_structured_entries(self):
        self.assertIn('manifest_target="$PANEL_DIR/app/$manifest_path"', self.launcher)
        self.assertIn('manifest_target="$PANEL_DIR/$manifest_path"', self.launcher)
        self.assertIn("manifest_owner=root:root", self.launcher)
        self.assertIn("manifest_mode=0755", self.launcher)
        self.assertIn('chown -- "$manifest_owner" "$manifest_target"', self.launcher)
        self.assertIn('chmod -- "$manifest_mode" "$manifest_target"', self.launcher)

    def test_parser_rejects_unsafe_or_ambiguous_metadata(self):
        self.assertIn('"$manifest_path" != /*', self.launcher)
        self.assertIn('"$manifest_path" != *..*', self.launcher)
        self.assertIn('"$manifest_path" != *//*', self.launcher)
        self.assertIn("owner and mode must be specified together", self.launcher)
        self.assertIn("invalid privileged manifest owner", self.launcher)
        self.assertIn("invalid privileged manifest mode", self.launcher)
        self.assertIn('! -L "$manifest_target"', self.launcher)
        self.assertIn("unexpected privileged manifest parser shape", self.launcher)

    def test_release_contains_reviewed_structured_entry_and_old_verifier(self):
        manifest = release_member("/app/privileged-files.txt")
        verifier = release_member("/app/security_utils.py")
        self.assertEqual(
            manifest.count("app/hostpanel-postgres-admin root:root 0755"),
            1,
        )
        self.assertEqual(
            verifier.count(
                "            line.strip() for line in manifest.read_text().splitlines()"
            ),
            1,
        )
        self.assertNotIn("fields = line.split()", verifier)

    def test_generated_installer_patches_privileged_verifier_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            generated = pathlib.Path(directory) / "install.generated.sh"
            subprocess.run(
                [sys.executable, str(HARDENER), str(BASE), str(generated)],
                cwd=ROOT,
                check=True,
            )
            patch = launcher_patch("PYPRIVVERIFY")
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
            text = generated.read_text(encoding="utf-8")
            self.assertEqual(text.count("PYPRIVVERIFYRUNTIME"), 2)
            self.assertIn("fields = line.split()", text)
            self.assertIn('if rel.startswith("app/")', text)
            self.assertIn("rel = rel[4:]", text)
            self.assertIn("malformed privileged-files.txt entry", text)
            self.assertIn("unexpected privileged verification parser shape", text)
            self.assertIn("python3 -m py_compile", text)
            subprocess.run(["bash", "-n", str(generated)], check=True)

    def test_verifier_patch_is_fail_closed_and_atomic(self):
        self.assertEqual(self.launcher.count("PYPRIVVERIFY"), 2)
        self.assertIn("unexpected privileged verifier injection shape", self.launcher)
        self.assertIn(".privileged-verifier-injection.", self.launcher)
        self.assertIn("os.replace(temporary, path)", self.launcher)

    def test_release_inspector_resolves_structured_paths(self):
        self.assertIn('fields = entry.split()', self.inspector)
        self.assertIn('if "/" in path', self.inspector)
        self.assertIn('release_prefix', self.inspector)
        self.assertIn('app_prefix', self.inspector)


if __name__ == "__main__":
    unittest.main()
