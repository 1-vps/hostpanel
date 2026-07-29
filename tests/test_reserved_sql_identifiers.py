import importlib.util
import pathlib
import subprocess
import sys
import tarfile
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
PATCHER_PATH = ROOT / "tools" / "patch_reserved_sql_identifiers.py"
HARDENER_PATH = ROOT / "tools" / "harden_install.py"
BASE_INSTALLER = ROOT / "install.base.sh"


def load_module(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PATCHER = load_module("hostpanel_reserved_sql_patcher", PATCHER_PATH)


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


class ReservedSqlIdentifierTests(unittest.TestCase):
    def test_signed_release_contains_reviewed_reserved_cursor_shape(self):
        store = release_member("/app/store.py")
        platform = release_member("/app/platform_store.py")
        self.assertEqual(store.count("    offset       INTEGER NOT NULL DEFAULT 0,"), 1)
        self.assertEqual(platform.count("    offset      INTEGER NOT NULL DEFAULT 0,"), 1)
        self.assertEqual(
            platform.count("SELECT inode,offset FROM platform_log_cursors WHERE source=?"),
            1,
        )
        self.assertEqual(platform.count('cursor["offset"]'), 2)
        self.assertEqual(
            platform.count(
                "INSERT INTO platform_log_cursors(source,inode,offset,updated) VALUES(?,?,?,?) "
            ),
            1,
        )

    def test_postprocessed_installer_is_idempotent_and_compiles(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = pathlib.Path(directory)
            generated = directory / "install.generated.sh"
            patched = directory / "install.patched.sh"
            patched_again = directory / "install.patched-again.sh"
            subprocess.run(
                [sys.executable, str(HARDENER_PATH), str(BASE_INSTALLER), str(generated)],
                cwd=ROOT,
                check=True,
            )
            subprocess.run(
                [sys.executable, str(PATCHER_PATH), str(generated), str(patched)],
                cwd=ROOT,
                check=True,
            )
            subprocess.run(["bash", "-n", str(patched)], check=True)
            subprocess.run(
                [sys.executable, str(PATCHER_PATH), str(patched), str(patched_again)],
                cwd=ROOT,
                check=True,
            )
            self.assertEqual(patched.read_bytes(), patched_again.read_bytes())
            text = patched.read_text(encoding="utf-8")
        self.assertEqual(text.count(PATCHER.RUNTIME_PATCH), 1)
        self.assertEqual(text.count(PATCHER.MARKER), 0)
        self.assertIn("cursor_offset INTEGER NOT NULL DEFAULT 0", text)
        self.assertIn("unexpected platform cursor upsert shape", text)
        self.assertIn('python3 -m py_compile "$PANEL_DIR/app/store.py"', text)

    def test_committed_installers_include_reserved_identifier_patch(self):
        for name in ("install.sh", "install-hardened.sh"):
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertEqual(text.count(PATCHER.RUNTIME_PATCH), 1, name)
            self.assertEqual(text.count(PATCHER.MARKER), 0, name)


if __name__ == "__main__":
    unittest.main()
