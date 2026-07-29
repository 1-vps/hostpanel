#!/usr/bin/env python3
"""Apply the reviewed initial-backup and doctor exit-code correction."""
from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
HARDENER = ROOT / "tools" / "harden_install.py"
TEST = ROOT / "tests" / "test_post_install_health.py"
WORKFLOW = ROOT / ".github" / "workflows" / "apply-post-install-health.yml"
SELF = pathlib.Path(__file__).resolve()

text = HARDENER.read_text(encoding="utf-8")
anchor = '''        "Rspamd secret permissions",
    )
    return text
'''
replacement = r'''        "Rspamd secret permissions",
    )

    text = _replace_once(
        text,
        '''HP_DB="$PANEL_DIR/hostpanel.db" HP_BACKUP_DIR="$BACKUP_DIR" \
  "$PANEL_DIR/venv/bin/python" "$PANEL_DIR/app/hostpanel-doctor" --quiet || die "Post-install health check failed"''',
        '''if has_role backup; then
  say "Creating the initial verified backup"
  HP_DB="$PANEL_DIR/hostpanel.db" HP_BACKUP_DIR="$BACKUP_DIR" \
    "$PANEL_DIR/venv/bin/python" "$PANEL_DIR/app/hostpanel-backup" >>"$LOG" 2>&1 \
    || die "Initial verified backup failed"
fi
DOCTOR_STATUS=0
HP_DB="$PANEL_DIR/hostpanel.db" HP_BACKUP_DIR="$BACKUP_DIR" \
  "$PANEL_DIR/venv/bin/python" "$PANEL_DIR/app/hostpanel-doctor" --quiet >>"$LOG" 2>&1 \
  || DOCTOR_STATUS=$?
case "$DOCTOR_STATUS" in
  0) ;;
  1) warn "Post-install health check completed with warnings; inspect $LOG" ;;
  *) die "Post-install health check failed" ;;
esac''',
        "initial backup and doctor exit semantics",
    )
    return text
'''
if text.count(anchor) != 1:
    raise SystemExit(f"unexpected hardener insertion anchor count: {text.count(anchor)}")
HARDENER.write_text(text.replace(anchor, replacement, 1), encoding="utf-8")

TEST.write_text(
    '''import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
HARDENER = ROOT / "tools" / "harden_install.py"
BASE = ROOT / "install.base.sh"


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

    @classmethod
    def tearDownClass(cls):
        cls.generated_path.unlink(missing_ok=True)

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
        self.assertIn('1) warn "Post-install health check completed with warnings; inspect $LOG" ;;', block)
        self.assertIn('*) die "Post-install health check failed" ;;', block)
        self.assertNotIn('--quiet || die "Post-install health check failed"', self.generated)

    def test_generated_installer_has_valid_bash_syntax(self):
        subprocess.run(["bash", "-n", str(self.generated_path)], check=True)


if __name__ == "__main__":
    unittest.main()
''',
    encoding="utf-8",
)

WORKFLOW.unlink(missing_ok=True)
SELF.unlink(missing_ok=True)
