#!/usr/bin/env python3
import pathlib
import subprocess
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
BOOTSTRAP = (ROOT / "bootstrap-install.sh").read_text(encoding="utf-8")
INSTALLER = (ROOT / "install.sh").read_text(encoding="utf-8")
HARDENER = (ROOT / "tools/harden_install.py").read_text(encoding="utf-8")


class Issue116BootstrapSecurityTests(unittest.TestCase):
    def test_privileged_path_and_loader_environment_are_sanitized_first(self) -> None:
        for text in (BOOTSTRAP, INSTALLER):
            self.assertLess(text.index("PATH=/usr/local/sbin"), text.index("command -v"))
            for name in ("PYTHONPATH", "PYTHONHOME", "BASH_ENV", "LD_PRELOAD", "LD_LIBRARY_PATH"):
                self.assertIn(name, text)

    def test_bootstrap_accepts_only_the_canonical_repository(self) -> None:
        self.assertIn(
            '[[ "$REPO" == "https://github.com/1-vps/hostpanel.git" ]]',
            BOOTSTRAP,
        )
        self.assertNotIn('^https://[^[:space:]]+$', BOOTSTRAP)
        self.assertNotIn("from $REPO", BOOTSTRAP)

    def test_git_credentials_are_cleared_after_exact_checkout(self) -> None:
        checkout = BOOTSTRAP.index('checkout -q --detach "$FETCHED_COMMIT"')
        cleanup = BOOTSTRAP.index("clear_git_auth_environment", checkout)
        checksums = BOOTSTRAP.index('CHECKSUMS="$CHECKOUT/SHA256SUMS"')
        self.assertLess(checkout, cleanup)
        self.assertLess(cleanup, checksums)
        self.assertIn("GIT_CONFIG_KEY_", BOOTSTRAP)
        self.assertIn("GIT_CONFIG_VALUE_", BOOTSTRAP)
        self.assertIn("GIT_CONFIG_NOSYSTEM=1", BOOTSTRAP)
        self.assertIn("GIT_CONFIG_GLOBAL=/dev/null", BOOTSTRAP)

    def test_reviewed_helpers_are_copied_into_one_source_root(self) -> None:
        for path in (
            "tools/harden_install_impl.py",
            "tools/patch_cli_runtime_env.py",
            "tools/hostpanel-update.py",
            "tools/install-update-agent.sh",
            "packaging/systemd/hostpanel-update.service",
            "packaging/systemd/hostpanel-update.timer",
            "releases/update.pub",
            "releases/update-keyring.json",
        ):
            self.assertIn(path, BOOTSTRAP)
        self.assertIn('HP_HARDENER_SOURCE_ROOT="$CHECKOUT"', BOOTSTRAP)

    def test_hardener_has_no_global_tmp_candidate_discovery(self) -> None:
        self.assertNotIn('pathlib.Path("/tmp").glob', HARDENER)
        self.assertNotIn("find /tmp", HARDENER)
        self.assertNotIn("hostpanel-bootstrap.*", HARDENER)
        self.assertIn("trusted_source_file", HARDENER)
        self.assertIn('os.environ.pop("HP_HARDENER_SOURCE_ROOT", "")', HARDENER)

    def test_noncanonical_repository_fails_before_fetch(self) -> None:
        result = subprocess.run(
            ["bash", str(ROOT / "bootstrap-install.sh"), "--check"],
            env={
                "PATH": "/usr/bin:/bin",
                "HP_REPO": "https://github.com.evil.invalid/1-vps/hostpanel.git",
                "HP_REPO_REF": "0" * 40,
            },
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("canonical HostPanel repository", result.stderr)
        self.assertNotIn("Fetching reviewed", result.stdout)


if __name__ == "__main__":
    unittest.main()
