import hashlib
import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
HARDENER = ROOT / "tools" / "harden_install.py"
IMPLEMENTATION = ROOT / "tools" / "harden_install_impl.py"
BASE = ROOT / "install.base.sh"
EXPECTED_IMPL_BLOB = "7b3749f00908545e106fdb1a305c243e03135d88"


def git_blob_sha(path: pathlib.Path) -> str:
    data = path.read_bytes()
    payload = f"blob {len(data)}\0".encode("ascii") + data
    return hashlib.sha1(payload, usedforsecurity=False).hexdigest()


class PostInstallHealthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.NamedTemporaryFile(prefix="hostpanel-hardened-", delete=False) as handle:
            cls.generated_path = pathlib.Path(handle.name)
        subprocess.run(
            ["python3", str(HARDENER), str(BASE), str(cls.generated_path)],
            cwd=ROOT,
            check=True,
        )
        cls.generated = cls.generated_path.read_text(encoding="utf-8")
        cls.wrapper = HARDENER.read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.generated_path.unlink(missing_ok=True)

    def test_blob_pinned_implementation_is_present(self):
        self.assertTrue(IMPLEMENTATION.is_file())
        self.assertFalse(IMPLEMENTATION.is_symlink())
        self.assertEqual(git_blob_sha(IMPLEMENTATION), EXPECTED_IMPL_BLOB)
        self.assertIn(f'EXPECTED_IMPL_BLOB = "{EXPECTED_IMPL_BLOB}"', self.wrapper)

    def test_initial_backup_precedes_doctor_for_backup_role(self):
        backup = self.generated.index('say "Creating the initial verified backup"')
        command = self.generated.index('"$PANEL_DIR/app/hostpanel-backup"', backup)
        doctor = self.generated.index('"$PANEL_DIR/app/hostpanel-doctor" --quiet', command)
        self.assertLess(backup, command)
        self.assertLess(command, doctor)
        self.assertIn('if has_role backup; then', self.generated[backup - 80:command])
        self.assertIn('|| die "Initial verified backup failed"', self.generated[command:doctor])

    def test_doctor_warning_and_failure_exit_codes_are_distinct(self):
        doctor = self.generated.index('DOCTOR_STATUS=0')
        block = self.generated[doctor:doctor + 700]
        self.assertIn('|| DOCTOR_STATUS=$?', block)
        self.assertIn(
            '1) warn "Post-install health check completed with warnings; inspect $LOG" ;;',
            block,
        )
        self.assertIn('*) die "Post-install health check failed" ;;', block)
        self.assertNotIn('--quiet || die "Post-install health check failed"', self.generated)

    def test_generated_installer_has_valid_bash_syntax(self):
        subprocess.run(["bash", "-n", str(self.generated_path)], check=True)


if __name__ == "__main__":
    unittest.main()
