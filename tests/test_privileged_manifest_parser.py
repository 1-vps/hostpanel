import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "install.sh"
INSPECTOR = ROOT / "tools" / "inspect-release-state.py"


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

    def test_release_inspector_resolves_structured_paths(self):
        self.assertIn('fields = entry.split()', self.inspector)
        self.assertIn('if "/" in path', self.inspector)
        self.assertIn('release_prefix', self.inspector)
        self.assertIn('app_prefix', self.inspector)


if __name__ == "__main__":
    unittest.main()
