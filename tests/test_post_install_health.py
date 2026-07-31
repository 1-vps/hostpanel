import pathlib
import subprocess
import tarfile
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
HARDENER = ROOT / "tools" / "harden_install.py"
IMPLEMENTATION = ROOT / "tools" / "harden_install_impl.py"
PATCHER = ROOT / "tools" / "patch_cli_runtime_env.py"
MATRIX = ROOT / "test-matrix.sh"
BASE = ROOT / "install.base.sh"
EXPECTED_IMPL_BLOB = "ac1a86fba8dd6bace70cd844a61bfccdda55d916"
EXPECTED_PATCHER_BLOB = "a20ce7252351179fa225a9a0b168b5fa9568883c"


def git_blob_sha(path: pathlib.Path) -> str:
    result = subprocess.run(
        ["git", "hash-object", "--no-filters", str(path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def extract_signed_source(suffix: str, destination: pathlib.Path) -> None:
    archives = sorted(ROOT.glob("hostpanel-*-source.tar.gz"))
    if len(archives) != 1:
        raise RuntimeError(f"expected one signed source archive, found {len(archives)}")
    with tarfile.open(archives[0], "r:gz") as archive:
        matches = [member for member in archive.getmembers() if member.name.endswith(suffix)]
        if len(matches) != 1:
            raise RuntimeError(f"expected one {suffix}, found {len(matches)}")
        handle = archive.extractfile(matches[0])
        if handle is None:
            raise RuntimeError(f"could not read {suffix}")
        destination.write_bytes(handle.read())


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
        cls.patcher = PATCHER.read_text(encoding="utf-8")
        cls.matrix = MATRIX.read_text(encoding="utf-8")

        cls.runtime_temp = tempfile.TemporaryDirectory(prefix="hostpanel-cli-runtime-")
        runtime_root = pathlib.Path(cls.runtime_temp.name)
        cls.backup_path = runtime_root / "hostpanel-backup"
        cls.doctor_path = runtime_root / "hostpanel-doctor"
        cls.mysql_admin_path = runtime_root / "hostpanel-mysql-admin"
        extract_signed_source("/app/hostpanel-backup", cls.backup_path)
        extract_signed_source("/app/hostpanel-doctor", cls.doctor_path)
        extract_signed_source("/app/hostpanel-mysql-admin", cls.mysql_admin_path)
        for _ in range(2):
            subprocess.run(
                [
                    "python3",
                    str(PATCHER),
                    str(cls.backup_path),
                    str(cls.doctor_path),
                    str(cls.mysql_admin_path),
                ],
                cwd=ROOT,
                check=True,
            )
        subprocess.run(
            [
                "python3",
                "-m",
                "py_compile",
                str(cls.backup_path),
                str(cls.doctor_path),
                str(cls.mysql_admin_path),
            ],
            check=True,
        )
        cls.patched_backup = cls.backup_path.read_text(encoding="utf-8")
        cls.patched_doctor = cls.doctor_path.read_text(encoding="utf-8")
        cls.patched_mysql_admin = cls.mysql_admin_path.read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.generated_path.unlink(missing_ok=True)
        cls.runtime_temp.cleanup()

    def test_blob_pinned_implementation_is_present(self):
        self.assertTrue(IMPLEMENTATION.is_file())
        self.assertFalse(IMPLEMENTATION.is_symlink())
        self.assertEqual(git_blob_sha(IMPLEMENTATION), EXPECTED_IMPL_BLOB)
        self.assertIn(f'EXPECTED_IMPL_BLOB = "{EXPECTED_IMPL_BLOB}"', self.wrapper)
        self.assertEqual(git_blob_sha(PATCHER), EXPECTED_PATCHER_BLOB)
        self.assertIn(
            f'EXPECTED_CLI_PATCHER_BLOB = "{EXPECTED_PATCHER_BLOB}"',
            self.wrapper,
        )

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

    def test_cli_environment_patcher_runs_before_backup(self):
        resolver = self.generated.index(
            'CLI_RUNTIME_PATCHER="$SOURCE_ROOT/tools/patch_cli_runtime_env.py"'
        )
        patcher = self.generated.index('python3 "$CLI_RUNTIME_PATCHER"', resolver)
        compile_check = self.generated.index(
            "Patched CLI runtime environment loaders do not compile", patcher
        )
        backup = self.generated.index('say "Creating the initial verified backup"')
        self.assertLess(resolver, patcher)
        self.assertLess(patcher, compile_check)
        self.assertLess(compile_check, backup)
        invocation = self.generated[patcher:compile_check]
        self.assertIn('"$PANEL_DIR/app/hostpanel-mysql-admin"', invocation)

    def test_cli_patcher_fallback_is_unique_and_blob_verified(self):
        self.assertIn(
            "/tmp/hostpanel-bootstrap.*/repository/tools/patch_cli_runtime_env.py",
            self.generated,
        )
        self.assertIn('((${#CLI_RUNTIME_PATCHERS[@]} == 1))', self.generated)
        self.assertIn('[[ -f "$CLI_RUNTIME_PATCHER" && ! -L "$CLI_RUNTIME_PATCHER" ]]', self.generated)
        self.assertIn(
            'git hash-object --no-filters "$CLI_RUNTIME_PATCHER"', self.generated
        )
        self.assertIn(EXPECTED_PATCHER_BLOB, self.generated)
        self.assertIn(
            "The CLI runtime environment patcher does not match the reviewed Git blob",
            self.generated,
        )

    def test_cli_environment_loader_is_strict_and_non_shell(self):
        self.assertIn("config.stat(follow_symlinks=False)", self.patcher)
        self.assertIn("metadata.st_uid != 0", self.patcher)
        self.assertIn("mode = _runtime_stat.S_IMODE(metadata.st_mode)", self.patcher)
        self.assertIn("mode not in (0o600, 0o640)", self.patcher)
        self.assertNotIn("metadata.st_mode & 0o022", self.patcher)
        self.assertIn("or key in seen", self.patcher)
        self.assertIn("_runtime_os.environ.setdefault(key, value)", self.patcher)
        self.assertIn(
            'required = ("HP_SECRET", "HP_DATABASE_URL_FILE", "HP_MASTER_KEY_FILE")',
            self.patcher,
        )
        self.assertNotIn('. "$PANEL_DIR/config.env"', self.generated)
        self.assertNotIn('source "$PANEL_DIR/config.env"', self.generated)

    def test_signed_clis_are_patched_idempotently_and_compile(self):
        for text in (self.patched_backup, self.patched_doctor):
            self.assertEqual(text.count("def _load_runtime_environment()"), 1)
            self.assertEqual(text.count("_load_runtime_environment()"), 2)
            self.assertIn('required = ("HP_SECRET", "HP_DATABASE_URL_FILE", "HP_MASTER_KEY_FILE")', text)
            self.assertIn("mode not in (0o600, 0o640)", text)
        self.assertLess(
            self.patched_backup.index("_load_runtime_environment()"),
            self.patched_backup.index("from modules import backups"),
        )
        self.assertLess(
            self.patched_doctor.index("_load_runtime_environment()"),
            self.patched_doctor.index("import osrelease"),
        )

    def test_mariadb_gateway_keeps_local_infile_disabled_portably(self):
        self.assertNotIn(
            'SET SESSION local_infile=0;', self.patched_mysql_admin
        )
        self.assertIn(
            'prelude = "SET SESSION sql_mode=\'NO_BACKSLASH_ESCAPES\';\\n"',
            self.patched_mysql_admin,
        )
        self.assertGreaterEqual(
            self.patched_mysql_admin.count('"--local-infile=0"'), 3
        )
        self.assertIn("local-infile=0", self.patched_mysql_admin)

    def test_doctor_aligns_with_optional_openlitespeed_fallback(self):
        self.assertNotIn(
            'expected.update({"lsws": True, service("apache"): False})',
            self.patched_doctor,
        )
        self.assertIn(
            '"lsws": Path("/usr/local/lsws/bin/lswsctrl").is_file()',
            self.patched_doctor,
        )
        self.assertIn('service("apache"): False', self.patched_doctor)

    def test_unused_postsrsd_package_is_not_installed(self):
        self.assertIn(
            "POSTFIX_PACKAGES=(postfix postfix-pcre opendkim)", self.generated
        )
        self.assertNotIn(
            "POSTFIX_PACKAGES=(postfix postfix-pcre opendkim postsrsd)",
            self.generated,
        )

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
