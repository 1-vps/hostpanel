from __future__ import annotations

import pathlib
import subprocess
import tarfile
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
PATCHER = ROOT / "tools" / "patch_product_fixes.py"


def signed_archive() -> pathlib.Path:
    archives = sorted(ROOT.glob("hostpanel-*-source.tar.gz"))
    if len(archives) != 1:
        raise RuntimeError(f"expected one signed source archive, found {len(archives)}")
    return archives[0]


class ProductFixTests(unittest.TestCase):
    def patched_source(self, directory: pathlib.Path) -> pathlib.Path:
        with tarfile.open(signed_archive(), "r:gz") as archive:
            archive.extractall(directory, filter="data")
        roots = [path for path in directory.iterdir() if path.is_dir()]
        self.assertEqual(len(roots), 1)
        root = roots[0]
        subprocess.run(["python3", str(PATCHER), str(root)], check=True)
        first = {
            relative: (root / relative).read_bytes()
            for relative in (
                "app/modules/mail.py",
                "app/modules/accounts.py",
                "app/static/panel.js",
                "tests/browser/run_server.py",
            )
        }
        subprocess.run(["python3", str(PATCHER), str(root)], check=True)
        for relative, content in first.items():
            self.assertEqual((root / relative).read_bytes(), content, relative)
        return root

    def test_security_performance_and_role_fixes_are_applied(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.patched_source(pathlib.Path(directory))
            mail = (root / "app/modules/mail.py").read_text(encoding="utf-8")
            accounts = (root / "app/modules/accounts.py").read_text(encoding="utf-8")
            panel = (root / "app/static/panel.js").read_text(encoding="utf-8")
            self.assertIn(
                'require(is_admin(user), "Only administrators can inspect the server mail queue")',
                mail,
            )
            self.assertIn("def describe(user: dict, *, include_disk: bool = False)", accounts)
            self.assertIn("directory_size(home) if include_disk else None", accounts)
            self.assertIn("return describe(account, include_disk=True)", accounts)
            self.assertIn("if(ROLE!=='admin') return", panel)
            self.assertIn("ROLE==='admin')loadOperationalStatus()", panel)
            self.assertNotIn("${bytes(u.disk_bytes)}", panel)

    def test_patcher_rejects_unreviewed_or_unsafe_roots(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            result = subprocess.run(
                ["python3", str(PATCHER), str(root)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertNotEqual(result.returncode, 0)
            link = root / "source-link"
            link.symlink_to(root, target_is_directory=True)
            result = subprocess.run(
                ["python3", str(PATCHER), str(link)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unsafe source root", result.stderr)

    def test_bootstrap_verifies_installs_and_runs_product_patcher(self):
        bootstrap = (ROOT / "bootstrap-install.sh").read_text(encoding="utf-8")
        self.assertIn("tools/patch_product_fixes.py", bootstrap)
        self.assertIn(
            'install -m 0755 "$CHECKOUT/tools/patch_product_fixes.py"', bootstrap
        )
        self.assertIn(
            'python3 "$SOURCE_ROOT/tools/patch_product_fixes.py" "$SOURCE_ROOT"',
            bootstrap,
        )
        self.assertLess(
            bootstrap.index('python3 "$SOURCE_ROOT/tools/patch_product_fixes.py"'),
            bootstrap.index('python3 "$SOURCE_ROOT/tools/patch_panel_ui.py"'),
        )


if __name__ == "__main__":
    unittest.main()
