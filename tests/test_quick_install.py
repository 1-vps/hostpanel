from __future__ import annotations

import pathlib
import re
import subprocess
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "quick-install.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "installer-hardening.yml"
SETUP = ROOT / "SETUP.md"
EXPECTED_RELEASE_COMMIT = "755dcd5e47b7c82404b267e8df4dec27626fe341"
EXPECTED_BOOTSTRAP_BLOB = "639fae60ddd5bec36f5e3167dd21733a412a69fd"
EXPECTED_VALIDATOR_BLOB = "2eefb797a50a0a2e2827ca5687ba83a2b4b3eec9"
AUTO_COMMIT = "1a86d380e7ebab287c767d183013b599cb116f7f"
AUTO_BLOB = "db23963e101b9194994da2ff8077b40a6b1cb99c"


class QuickInstallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = INSTALLER.read_text(encoding="utf-8")
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")
        cls.setup = SETUP.read_text(encoding="utf-8")

    def test_shell_syntax_and_help(self) -> None:
        subprocess.run(["bash", "-n", str(INSTALLER)], check=True)
        result = subprocess.run(
            ["bash", str(INSTALLER), "--help"],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("--hostname FQDN", result.stdout)
        self.assertIn("Contents: Read-only", result.stdout)

    def test_installer_is_pinned_to_reviewed_objects(self) -> None:
        self.assertIn(f'REVIEWED_COMMIT_SHA="{EXPECTED_RELEASE_COMMIT}"', self.source)
        self.assertIn(f'BOOTSTRAP_BLOB="{EXPECTED_BOOTSTRAP_BLOB}"', self.source)
        self.assertIn(f'VALIDATOR_BLOB="{EXPECTED_VALIDATOR_BLOB}"', self.source)
        self.assertNotIn('HP_REPO_REF:-', self.source)
        self.assertIn('HP_REPO_REF="$REVIEWED_COMMIT_SHA"', self.source)

    def test_token_is_not_accepted_in_url_or_normal_arguments(self) -> None:
        self.assertNotRegex(self.source, r"--token\b")
        self.assertNotRegex(self.source.lower(), r"[?&]token=")
        self.assertIn("HP_GITHUB_TOKEN_FILE", self.source)
        self.assertIn("HP_GITHUB_TOKEN_FD", self.source)
        self.assertIn("read -r -s -p", self.source)
        self.assertIn('unset GIT_CONFIG_COUNT GIT_CONFIG_KEY_0', self.source)

    def test_downloads_use_private_api_https_and_blob_verification(self) -> None:
        self.assertIn('REPOSITORY_API="https://api.github.com/repos/1-vps/hostpanel"', self.source)
        self.assertIn("--proto '=https' --tlsv1.2", self.source)
        self.assertIn("application/vnd.github.raw+json", self.source)
        self.assertIn('git hash-object --no-filters "$WORK_DIR/bootstrap-install.sh"', self.source)
        self.assertIn('git hash-object --no-filters "$WORK_DIR/validate-production-vm.sh"', self.source)
        self.assertNotIn("raw.githubusercontent.com", self.source)

    def test_preflight_always_precedes_mutating_install(self) -> None:
        preflight = 'env "${common_env[@]}" bash "$WORK_DIR/bootstrap-install.sh" --check "${install_args[@]}"'
        persist_bootstrap = 'install -o root -g root -m 0700 "$WORK_DIR/bootstrap-install.sh" /root/bootstrap-install.sh'
        mutation = 'env "${common_env[@]}" bash /root/bootstrap-install.sh "${install_args[@]}"'
        self.assertIn(preflight, self.source)
        self.assertIn(persist_bootstrap, self.source)
        self.assertIn(mutation, self.source)
        self.assertLess(self.source.index(preflight), self.source.index(persist_bootstrap))
        self.assertLess(self.source.index(persist_bootstrap), self.source.index(mutation))
        self.assertIn('if [[ "$CHECK_ONLY" == yes ]]', self.source)

    def test_root_environment_is_sanitized(self) -> None:
        self.assertIn(
            "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            self.source,
        )
        for variable in ("PYTHONPATH", "PYTHONHOME", "BASH_ENV", "LD_PRELOAD"):
            self.assertIn(variable, self.source)
        self.assertIn("umask 077", self.source)

    def test_setup_documents_current_immutable_automatic_entry(self) -> None:
        self.assertIn("`auto-install.sh` is the only documented HostPanel installation entry point", self.setup)
        self.assertIn(AUTO_COMMIT, self.setup)
        self.assertIn(AUTO_BLOB, self.setup)
        self.assertIn("authenticated checkout or", self.setup)
        self.assertIn("reviewed file transfer", self.setup)
        self.assertNotIn("quick-install.sh", self.setup)
        self.assertNotIn("quick-install.sh?ref=main", self.setup)
        self.assertNotIn("raw.githubusercontent.com", self.setup)

    def test_workflow_parses_and_shellchecks_quick_installer(self) -> None:
        self.assertRegex(
            self.workflow,
            re.compile(r"bash -n quick-install\.sh", re.MULTILINE),
        )
        shellcheck_block = self.workflow[self.workflow.index("shellcheck -S error") :]
        self.assertIn("quick-install.sh", shellcheck_block)


if __name__ == "__main__":
    unittest.main()
