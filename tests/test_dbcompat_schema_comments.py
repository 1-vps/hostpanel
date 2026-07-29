import importlib.util
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


def load_module(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HARDENER = load_module("hostpanel_hardener_wrapper", HARDENER_PATH)


def release_dbcompat_source() -> str:
    archives = sorted(ROOT.glob("hostpanel-*-source.tar.gz"))
    if len(archives) != 1:
        raise AssertionError(f"expected one release tarball, found {len(archives)}")
    with tarfile.open(archives[0], "r:gz") as archive:
        members = [
            member
            for member in archive.getmembers()
            if member.isfile() and member.name.endswith("/app/dbcompat.py")
        ]
        if len(members) != 1:
            raise AssertionError(f"expected one dbcompat.py, found {len(members)}")
        handle = archive.extractfile(members[0])
        if handle is None:
            raise AssertionError("could not extract dbcompat.py")
        return handle.read().decode("utf-8")


class RecordingConnection:
    def __init__(self):
        self.created = []

    def execute(self, statement):
        classified = re.sub(r"--[^\n]*(?:\n|$)", "\n", statement)
        classified = re.sub(r"/\*.*?\*/", " ", classified, flags=re.S)
        match = re.search(
            r"\bCREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+([A-Za-z_][A-Za-z0-9_]*)",
            classified,
            re.I,
        )
        if not match:
            return None
        name = match.group(1).lower()
        references = {
            value.lower()
            for value in re.findall(
                r"REFERENCES\s+([A-Za-z_][A-Za-z0-9_]*)", statement, re.I
            )
        } - {name}
        missing = references - set(self.created)
        if missing:
            raise AssertionError(
                f"table {name} executed before dependencies {sorted(missing)}"
            )
        self.created.append(name)
        return None


class DbcompatSchemaCommentTests(unittest.TestCase):
    def test_signed_release_contains_the_reviewed_bug_shape(self):
        source = release_dbcompat_source()
        self.assertEqual(source.count(HARDENER.DBCOMPAT_CLASSIFIER_OLD), 1)
        self.assertEqual(source.count(HARDENER.DBCOMPAT_CLASSIFIER_NEW), 0)

    def test_patched_adapter_orders_line_commented_parent_first(self):
        source = release_dbcompat_source().replace(
            HARDENER.DBCOMPAT_CLASSIFIER_OLD,
            HARDENER.DBCOMPAT_CLASSIFIER_NEW,
            1,
        )
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "dbcompat.py"
            path.write_text(source, encoding="utf-8")
            module = load_module("patched_dbcompat_line_comment", path)
            connection = RecordingConnection()
            module.PGConnection(connection).executescript(
                """
                CREATE TABLE IF NOT EXISTS child (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    parent_id INTEGER REFERENCES parent(id)
                );
                -- Parent table is intentionally declared after its child.
                CREATE TABLE IF NOT EXISTS parent (
                    id INTEGER PRIMARY KEY AUTOINCREMENT
                );
                """
            )
            self.assertEqual(connection.created[:2], ["parent", "child"])

    def test_patched_adapter_orders_block_commented_parent_first(self):
        source = release_dbcompat_source().replace(
            HARDENER.DBCOMPAT_CLASSIFIER_OLD,
            HARDENER.DBCOMPAT_CLASSIFIER_NEW,
            1,
        )
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "dbcompat.py"
            path.write_text(source, encoding="utf-8")
            module = load_module("patched_dbcompat_block_comment", path)
            connection = RecordingConnection()
            module.PGConnection(connection).executescript(
                """
                CREATE TABLE IF NOT EXISTS child (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    parent_id INTEGER REFERENCES parent(id)
                );
                /* Parent table comment. */
                CREATE TABLE IF NOT EXISTS parent (
                    id INTEGER PRIMARY KEY AUTOINCREMENT
                );
                """
            )
            self.assertEqual(connection.created[:2], ["parent", "child"])

    def test_generated_installer_applies_atomic_fail_closed_patch(self):
        with tempfile.TemporaryDirectory() as directory:
            generated = pathlib.Path(directory) / "install.generated.sh"
            subprocess.run(
                [sys.executable, str(HARDENER_PATH), str(BASE_INSTALLER), str(generated)],
                check=True,
                cwd=ROOT,
            )
            text = generated.read_text(encoding="utf-8")
        self.assertIn("unexpected dbcompat classifier shape", text)
        self.assertIn("os.replace(temporary, path)", text)
        self.assertIn('python3 -m py_compile "$PANEL_DIR/app/dbcompat.py"', text)
        self.assertIn("classified = re.sub(", text)


if __name__ == "__main__":
    unittest.main()
