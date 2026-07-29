import pathlib
import re
import subprocess
import sys
import tarfile
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
HARDENER_PATH = ROOT / "tools" / "harden_install.py"
BASE_INSTALLER = ROOT / "install.base.sh"


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


def match_count(pattern: str, text: str) -> int:
    return len(re.findall(pattern, text, flags=re.MULTILINE))


class ReservedSqlIdentifierTests(unittest.TestCase):
    def test_signed_release_contains_reviewed_cursor_shapes(self):
        store = release_member("/app/store.py")
        platform_store = release_member("/app/platform_store.py")
        platform_worker = release_member("/app/platform_worker.py")
        self.assertEqual(
            match_count(r"^\s*offset\s+INTEGER\s+NOT\s+NULL\s+DEFAULT\s+0,\s*$", store),
            1,
        )
        self.assertEqual(
            match_count(
                r"^\s*offset\s+INTEGER\s+NOT\s+NULL\s+DEFAULT\s+0,\s*$",
                platform_store,
            ),
            1,
        )
        self.assertIn("traffic_cursors", store)
        self.assertIn("platform_log_cursors", platform_store)
        self.assertEqual(
            platform_worker.count(
                "SELECT inode,offset FROM platform_log_cursors WHERE source=?"
            ),
            1,
        )
        self.assertEqual(platform_worker.count('cursor["offset"]'), 2)
        self.assertEqual(
            platform_worker.count(
                "INSERT INTO platform_log_cursors(source,inode,offset,updated) VALUES(?,?,?,?) "
            ),
            1,
        )
        self.assertEqual(
            platform_worker.count(
                "ON CONFLICT(source) DO UPDATE SET inode=excluded.inode,offset=excluded.offset,updated=excluded.updated"
            ),
            1,
        )

    def test_generated_installer_contains_fail_closed_runtime_patch(self):
        with tempfile.TemporaryDirectory() as directory:
            generated = pathlib.Path(directory) / "install.generated.sh"
            subprocess.run(
                [sys.executable, str(HARDENER_PATH), str(BASE_INSTALLER), str(generated)],
                cwd=ROOT,
                check=True,
            )
            subprocess.run(["bash", "-n", str(generated)], check=True)
            text = generated.read_text(encoding="utf-8")

        self.assertEqual(text.count("PYRESERVEDSQL"), 2)
        self.assertIn("Could not apply the reviewed reserved SQL identifier patch", text)
        self.assertIn("unexpected {label} shape in {path.name}", text)
        self.assertIn("traffic cursor schema", text)
        self.assertIn("platform log cursor schema", text)
        self.assertIn("platform_worker.py", text)
        self.assertIn("cursor_offset INTEGER NOT NULL DEFAULT 0", text)
        self.assertIn("SELECT inode,cursor_offset FROM platform_log_cursors", text)
        self.assertIn('cursor["cursor_offset"]', text)
        self.assertIn(
            "INSERT INTO platform_log_cursors(source,inode,cursor_offset,updated)",
            text,
        )
        self.assertIn("cursor_offset=excluded.cursor_offset", text)
        self.assertIn("os.replace(temporary, path)", text)
        self.assertIn(
            'python3 -m py_compile "$PANEL_DIR/app/store.py" "$PANEL_DIR/app/platform_store.py" "$PANEL_DIR/app/platform_worker.py"',
            text,
        )

    def test_launcher_uses_the_updated_hardener(self):
        launcher = (ROOT / "install.sh").read_text(encoding="utf-8")
        bootstrap = (ROOT / "bootstrap-install.sh").read_text(encoding="utf-8")
        self.assertIn('HARDENER="$SCRIPT_DIR/tools/harden_install.py"', launcher)
        self.assertIn('python3 "$HARDENER" "$BASE_INSTALLER" "$GENERATED_INSTALLER"', launcher)
        self.assertIn("tools/harden_install.py", bootstrap)
        self.assertIn(
            'install -m 0755 "$CHECKOUT/tools/harden_install.py" "$SOURCE_ROOT/tools/harden_install.py"',
            bootstrap,
        )


if __name__ == "__main__":
    unittest.main()
