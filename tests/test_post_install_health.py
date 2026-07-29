import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
HARDENER = ROOT / "tools" / "harden_install.py"
IMPLEMENTATION = ROOT / "tools" / "harden_install_impl.py"
MATRIX = ROOT / "test-matrix.sh"
BASE = ROOT / "install.base.sh"
EXPECTED_IMPL_BLOB = "7b3749f00908545e106fdb1a305c243e03135d88"


def git_blob_sha(path: pathlib.Path) -> str:
    result = subprocess.run(
        ["git", "hash-object", "--no-filters", str(path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


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
        cls.matrix = MATRIX.read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.generated_path.unlink(missing_ok=True)

    def test_blob_pinned_implementation_is_present(self):
        self.assertTrue(IMPLEMENTATION.is_file())
        self.assertFalse(IMPLEMENTATION.is_symlink())
        self.assertEqual(git_blob_sha(IMPLEMENTATION), EXPECTED_IMPL_BLOB)
        self.assertIn(f'EXPECTED_IMPL_BLOB = "{EXPECTED_IMPL_BLOB}"', self.wrapper)

    def test_os_matrix_copies_the_pinned_implementation(self):
        wrapper_copy = self.matrix.index(
            'docker cp "$SCRIPT_DIR/tools/harden_install.py"'
        )
        implementation_copy = self.matrix.index(
            'docker cp "$SCRIPT_DIR/tools/harden_install_impl.py"'
        )
        runtime_copy = self.matrix.index(
            'docker cp "$SCRIPT_DIR/tools/harden_install_runtime.py"'
        )
        self.assertLess(wrapper_copy, implementation_copy)
        self.assertLess(implementation_copy, runtime_copy)

    def test_cli_environment_loader_is_installed_before_backup(self):
        injection = self.generated.index(
            "Could not apply the reviewed CLI runtime environment patch"
        )
        compile_check = self.generated.index(
            "Patched CLI runtime environment loaders do not compile", injection
        )
        backup = self.generated.index('say "Creating the initial verified backup"')
        self.assertLess(injection, compile_check)
        self.assertLess(compile_check, backup)
        self.assertEqual(self.generated.count("_load_runtime_environment()"), 4)

    def test_cli_environment_loader_is_strict_and_non_shell(self):
        loader = self.generated.index(
            '_RUNTIME_ENV_KEY = _runtime_re.compile(r"HP_[A-Z0-9_]{1,128}")'
        )
        block = self.generated[loader:loader + 1900]
        self.assertIn("config.stat(follow_symlinks=False)", block)
        self.assertIn("metadata.st_uid != 0", block)
        self.assertIn("metadata.st_mode & 0o022", block)
        self.assertIn("or key in seen", block)
        self.assertIn("_runtime_os.environ.setdefault(key, value)", block)
        self.assertIn(
            'required = ("HP_SECRET", "HP_DATABASE_URL_FILE", "HP_MASTER_KEY_FILE")',
            block,
        )
        self.assertNotIn('. "$PANEL_DIR/config.env"', self.generated)
        self.assertNotIn('source "$PANEL_DIR/config.env"', self.generated)

    def test_initial_backup_precedes_doctor_for_backup_role(self):
        backup = self.generated.index('say "Creating the initial verified backup"')
        command = self.generated.index('"$PANEL_DIR/app/hostpanel-backup"', backup)
        doctor = self.generated.index('"$PANEL_DIR/app/hostpanel-doctor" --quiet', command)
        self.assertLess(backup, command)
        self.assertLess(command, doctor)
        self.assertIn('if has_role backup; then', self.generated[backup - 80:command])
        self.assertIn('|| die "Initial verified backup failed"', self.generated[command:doctor])

    def test_initial_backup_and_doctor_use_production_environment(self):
        backup = self.generated.index('say "Creating the initial verified backup"')
        doctor = self.generated.index('DOCTOR_STATUS=0', backup)
        backup_block = self.generated[backup:doctor]
        doctor_block = self.generated[doctor:doctor + 1100]
        for variable in (
            'HP_DATABASE_URL_FILE="$PANEL_DIR/credentials/database-url"',
            'HP_MASTER_KEY_FILE="$PANEL_DIR/credentials/master.key"',
            'HP_VHOST_ROOT="$VHOST_ROOT"',
            'HP_BACKUP_DIR="$BACKUP_DIR"',
        ):
            self.assertIn(variable, backup_block)
            self.assertIn(variable, doctor_block)

    def test_backup_and_doctor_cron_use_production_environment(self):
        lines = self.generated.splitlines()
        for schedule in ("0 3 * * *", "30 3 * * *", "0 6 * * 1"):
            line = next(line for line in lines if line.startswith(schedule))
            self.assertIn(
                "HP_DATABASE_URL_FILE=$PANEL_DIR/credentials/database-url", line
            )
            self.assertIn(
                "HP_MASTER_KEY_FILE=$PANEL_DIR/credentials/master.key", line
            )
            self.assertIn("HP_VHOST_ROOT=$VHOST_ROOT", line)
            self.assertIn("HP_BACKUP_DIR=$BACKUP_DIR", line)

    def test_doctor_warning_and_failure_exit_codes_are_distinct(self):
        doctor = self.generated.index('DOCTOR_STATUS=0')
        block = self.generated[doctor:doctor + 1100]
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
