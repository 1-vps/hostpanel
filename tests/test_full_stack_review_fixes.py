#!/usr/bin/env python3
import pathlib
import subprocess
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class FullStackReviewFixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.qemu = (ROOT / "tools/run-qemu-vm-acceptance.sh").read_text(
            encoding="utf-8"
        )
        cls.bootstrap = (ROOT / "bootstrap-install.sh").read_text(encoding="utf-8")
        cls.installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        cls.validator = (ROOT / "tools/validate-production-vm.sh").read_text(
            encoding="utf-8"
        )
        cls.ui = (ROOT / "app/static/panel-redesign.js").read_text(
            encoding="utf-8"
        )

    def test_qemu_token_is_deexported_before_logger_process_starts(self) -> None:
        assignment = self.qemu.index('REPO_TOKEN="${HP_QEMU_REPO_TOKEN:-}"')
        deexport = self.qemu.index(
            "export -n HP_QEMU_REPO_TOKEN REPO_TOKEN 2>/dev/null || true"
        )
        logger = self.qemu.index('exec > >(tee "$ARTIFACT_DIR/runner.log") 2>&1')
        unset = self.qemu.index("unset HP_QEMU_REPO_TOKEN", logger)
        self.assertLess(assignment, deexport)
        self.assertLess(deexport, logger)
        self.assertLess(logger, unset)

    def test_deexported_variables_are_absent_from_child_environment(self) -> None:
        shell = r'''
set -euo pipefail
export HP_QEMU_REPO_TOKEN=review-secret
REPO_TOKEN="$HP_QEMU_REPO_TOKEN"
export REPO_TOKEN
export -n HP_QEMU_REPO_TOKEN REPO_TOKEN
python3 - <<'PY'
import os
assert "HP_QEMU_REPO_TOKEN" not in os.environ
assert "REPO_TOKEN" not in os.environ
PY
'''
        completed = subprocess.run(
            ["bash", "-c", shell],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_launchers_use_distinct_exit_and_signal_traps(self) -> None:
        for name, text in (
            ("bootstrap", self.bootstrap),
            ("installer", self.installer),
        ):
            with self.subTest(script=name):
                self.assertIn("signal_exit(){", text)
                self.assertIn("trap cleanup EXIT", text)
                self.assertIn("trap 'signal_exit 129' HUP", text)
                self.assertIn("trap 'signal_exit 130' INT", text)
                self.assertIn("trap 'signal_exit 143' TERM", text)
                self.assertNotIn("trap cleanup EXIT HUP INT TERM", text)
                self.assertIn('kill -TERM "$INSTALLER_PID"', text)
                self.assertIn('wait "$INSTALLER_PID"', text)
                self.assertIn("INSTALLER_PID=$!", text)

    def test_signal_handler_preserves_signal_exit_status(self) -> None:
        shell = r'''
set -euo pipefail
child=""
cleanup(){ :; }
signal_exit(){
  local status="$1"
  trap - EXIT HUP INT TERM
  if [[ "$child" =~ ^[0-9]+$ ]]; then
    kill -TERM "$child" 2>/dev/null || true
    wait "$child" 2>/dev/null || true
  fi
  cleanup
  exit "$status"
}
trap cleanup EXIT
trap 'signal_exit 143' TERM
sleep 30 & child=$!
# Let the background process finish exec before testing the trap so it cannot
# retain the captured pipes through a launch race in the test harness itself.
sleep 0.1
kill -TERM $$
wait "$child"
'''
        completed = subprocess.run(
            ["bash", "-c", shell],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        self.assertEqual(completed.returncode, 143, completed.stderr)

    def test_installed_openlitespeed_is_a_required_active_unit(self) -> None:
        self.assertIn("apache2 httpd lsws postgresql", self.validator)
        self.assertIn("OpenLiteSpeed is installed but lsws.service is missing", self.validator)

    def test_dashboard_actions_resync_with_permission_changes(self) -> None:
        self.assertIn("function syncPageLinkVisibility()", self.ui)
        self.assertIn("function pageLinkAllowed(navLink)", self.ui)
        self.assertIn("new MutationObserver(syncPageLinkVisibility).observe(nav", self.ui)
        self.assertIn("attributeFilter:['hidden','aria-hidden','class','style']", self.ui)
        self.assertIn("if(!page||!pageLinkAllowed(navLink))", self.ui)

    def test_selected_locale_precedes_document_default(self) -> None:
        self.assertIn(
            "picker?.value||document.documentElement.lang||'en'",
            self.ui,
        )
        self.assertIn("activeLanguage=currentLanguage();", self.ui)


if __name__ == "__main__":
    unittest.main()
