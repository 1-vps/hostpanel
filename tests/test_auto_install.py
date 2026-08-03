from __future__ import annotations

import pathlib
import re
import subprocess
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "auto-install.sh"
QUICK = ROOT / "quick-install.sh"
SETUP = ROOT / "SETUP.md"
CUSTOMBUILD = ROOT / "CUSTOMBUILD.md"
CLOUD_INIT = ROOT / "examples" / "cloud-init-hostpanel.yaml"
WORKFLOW = ROOT / ".github" / "workflows" / "automatic-installer.yml"
AUTO_COMMIT = "1a86d380e7ebab287c767d183013b599cb116f7f"
AUTO_BLOB = "db23963e101b9194994da2ff8077b40a6b1cb99c"
QUICK_COMMIT = "7c7089da057c0f301ce98ead35f988435e10a0b6"
QUICK_BLOB = "a560d8da6ede9a68b63db5d36644a011c62d0b23"
PRODUCT_COMMIT = "755dcd5e47b7c82404b267e8df4dec27626fe341"


class AutomaticInstallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = INSTALLER.read_text(encoding="utf-8")
        cls.quick = QUICK.read_text(encoding="utf-8")
        cls.setup = SETUP.read_text(encoding="utf-8")
        cls.setup_words = " ".join(cls.setup.split())
        cls.custombuild = CUSTOMBUILD.read_text(encoding="utf-8")
        cls.cloud_init = CLOUD_INIT.read_text(encoding="utf-8")
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_shell_syntax_and_noninteractive_help(self) -> None:
        for script in (INSTALLER, QUICK):
            subprocess.run(["bash", "-n", str(script)], check=True)
        result = subprocess.run(
            ["bash", str(INSTALLER), "--help"],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("never prompts", result.stdout)
        self.assertIn("check-only", result.stdout)
        self.assertIn("unverified", result.stdout)
        quick_help = subprocess.run(
            ["bash", str(QUICK), "--help"],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("base web stack is always nginx_apache", quick_help.stdout)

    def test_all_execution_inputs_are_pinned(self) -> None:
        self.assertIn(f'QUICK_INSTALL_COMMIT="{QUICK_COMMIT}"', self.source)
        self.assertIn(f'QUICK_INSTALL_BLOB="{QUICK_BLOB}"', self.source)
        self.assertIn(f'PRODUCT_COMMIT="{PRODUCT_COMMIT}"', self.source)
        self.assertIn('VALIDATOR_BLOB="2eefb797a50a0a2e2827ca5687ba83a2b4b3eec9"', self.source)
        self.assertIn(f'REVIEWED_COMMIT_SHA="{PRODUCT_COMMIT}"', self.quick)
        self.assertNotIn("ref=main", self.source)
        self.assertNotIn("ref=main", self.quick)

    def test_fd_is_consumed_and_not_inherited_by_quick_installer(self) -> None:
        close = self.source.index('eval "exec ${TOKEN_FD}<&-"')
        clear = self.source.index('unset HP_GITHUB_TOKEN_FD', close)
        delegate = self.source.index('env -u HP_GITHUB_TOKEN_FD', clear)
        self.assertLess(close, clear)
        self.assertLess(clear, delegate)
        self.assertIn('HP_GITHUB_TOKEN_FILE="$PRIVATE_TOKEN_FILE"', self.source)
        self.assertIn('unset HP_GITHUB_TOKEN_FD', self.quick)

    def test_authenticated_curl_ignores_default_configuration(self) -> None:
        self.assertIn("curl -q --proto '=https' --tlsv1.2", self.source)
        self.assertIn("curl -q --proto '=https' --tlsv1.2", self.quick)
        self.assertNotRegex(self.source, re.compile(r"\bcurl --proto"))
        self.assertNotRegex(self.quick, re.compile(r"\bcurl --proto"))

    def test_check_only_avoids_persistent_mutation(self) -> None:
        self.assertIn("check-only never installs packages", self.source)
        self.assertIn("check-only never installs packages", self.quick)
        self.assertIn('[[ "$CHECK_ONLY" == no ]] || return 0', self.source)
        self.assertIn('bash "$WORK_DIR/bootstrap-install.sh" --check', self.quick)
        check_exit = self.quick.index('if [[ "$CHECK_ONLY" == yes ]]')
        root_install = self.quick.index('install -o root -g root -m 0700 "$WORK_DIR/bootstrap-install.sh"')
        custombuild_install = self.quick.index('bash "$PRODUCT_DIR/tools/install-hostpanel-build.sh"')
        self.assertLess(check_exit, root_install)
        self.assertLess(root_install, custombuild_install)
        self.assertIn("No packages or persistent installer files were changed", self.source)

    def test_base_install_is_always_nginx_apache(self) -> None:
        self.assertIn('HP_WEBSERVER_MODE=nginx_apache', self.quick)
        self.assertIn('printf \'  webserver:      nginx_apache\\n\'', self.quick)
        self.assertIn('Installing HostPanel with nginx and Apache', self.quick)
        self.assertNotIn('https://repo.litespeed.sh', self.quick)
        self.assertNotIn('HP_LITESPEED_REPO', self.quick)
        self.assertNotIn('ensure_litespeed_repository', self.quick)
        self.assertIn('base installation always uses **`nginx_apache`**', self.setup)
        self.assertIn('OpenLiteSpeed is not installed', self.setup)
        self.assertNotIn('HP_LITESPEED_REPO=auto', self.setup)
        self.assertNotIn('HP_LITESPEED_REPO=auto', self.cloud_init)

    def test_custombuild_inputs_are_blob_verified(self) -> None:
        self.assertIn('readonly CUSTOMBUILD_PATHS=(', self.quick)
        self.assertIn('readonly CUSTOMBUILD_BLOBS=(', self.quick)
        self.assertIn('tools/hostpanel_build_ssl.py', self.quick)
        self.assertIn('git hash-object --no-filters "$destination"', self.quick)
        self.assertIn('CustomBuild Git blob verification failed', self.quick)
        self.assertIn('python3 -m py_compile', self.quick)
        self.assertIn('bash -n "$PRODUCT_DIR/tools/install-hostpanel-build.sh"', self.quick)
        self.assertIn('hostpanel-build', self.quick)

    def test_explicit_domain_is_authoritative_and_fails_closed(self) -> None:
        domain_branch = self.source.index('elif [[ -n "$PANEL_DOMAIN" ]]')
        invalid = self.source.index('die "Invalid HP_PANEL_DOMAIN', domain_branch)
        fallback = self.source.index('PANEL_HOST="$(detect_host', invalid)
        self.assertLess(domain_branch, invalid)
        self.assertLess(invalid, fallback)
        self.assertNotIn('validate_hostname "panel.${PANEL_DOMAIN#.}" 2>/dev/null && return 0', self.source)
        self.assertNotIn("HP_ALLOW_PUBLIC_PANEL=yes", self.source)

    def test_doctor_is_required_and_skips_are_unverified(self) -> None:
        self.assertIn('[[ -x /opt/hostpanel/venv/bin/python ]] || die', self.source)
        self.assertIn('[[ -f /opt/hostpanel/app/hostpanel-doctor ]] || die', self.source)
        self.assertIn('/opt/hostpanel/app/hostpanel-doctor --quiet', self.source)
        self.assertIn('record_status unverified', self.source)

    def test_healthy_rerun_checks_before_credentials(self) -> None:
        installed_branch = self.source.index('if [[ -n "$installed" && "$REINSTALL" == no')
        credentials = self.source.index('PHASE="reading-credentials"')
        self.assertLess(installed_branch, credentials)
        self.assertIn("Existing HostPanel installation is healthy", self.source)

    def test_lock_precedes_package_mutation(self) -> None:
        bootstrap_lock = self.source.rindex("\nacquire_bootstrap_lock\n")
        prerequisites = self.source.rindex("\nensure_prerequisites\n")
        runtime_lock = self.source.rindex("\nacquire_lock\n")
        self.assertLess(bootstrap_lock, prerequisites)
        self.assertLess(prerequisites, runtime_lock)

    def test_setup_documents_only_fixed_automatic_engine(self) -> None:
        self.assertIn("`auto-install.sh` is the only documented HostPanel installation entry point", self.setup)
        self.assertIn(AUTO_COMMIT, self.setup)
        self.assertIn(AUTO_BLOB, self.setup)
        self.assertIn("descriptor is consumed", self.setup)
        self.assertIn("marked `unverified`", self.setup)
        self.assertIn("sudo hostpanel-build set webserver openlitespeed", self.setup)
        self.assertIn("package-candidate preflight before service mutation", self.setup_words)
        self.assertIn("complete matching LSPHP package", self.setup_words)
        self.assertIn("nginx remains the public HTTP/TLS edge", self.setup_words)
        self.assertIn("HTTP-to-HTTPS redirect behavior", self.setup_words)
        self.assertNotIn("install-one-line.sh", self.setup)
        self.assertNotIn("quick-install.sh", self.setup)
        self.assertNotIn("auto-install.sh?ref=main", self.setup)
        for mode in ("nginx_apache", "nginx", "apache", "openlitespeed"):
            self.assertIn(mode, self.custombuild)
        for provider in ("letsencrypt", "zerossl"):
            self.assertIn(f"--provider {provider}", self.custombuild)
        self.assertIn("zerossl-eab-kid", self.custombuild)
        self.assertIn("zerossl-eab-hmac", self.custombuild)

    def test_cloud_init_pins_and_verifies_launcher(self) -> None:
        self.assertIn("#cloud-config", self.cloud_init)
        self.assertIn(f"auto-install.sh?ref={AUTO_COMMIT}", self.cloud_init)
        self.assertIn(AUTO_BLOB, self.cloud_init)
        self.assertIn('git hash-object "$D/install"', self.cloud_init)
        self.assertIn("curl -q --proto '=https'", self.cloud_init)
        self.assertIn("nginx_apache provisioning", self.cloud_init)
        self.assertNotIn("ref=main", self.cloud_init)
        self.assertNotRegex(self.cloud_init, r"github_pat_[A-Za-z0-9_]+")

    def test_read_only_workflow_checks_installer_chain(self) -> None:
        self.assertIn("permissions:\n  contents: read", self.workflow)
        self.assertIn("persist-credentials: false", self.workflow)
        for path in (
            "auto-install.sh", "quick-install.sh", "install-one-line.sh",
            "tools/install-hostpanel-build.sh",
        ):
            self.assertIn(f"bash -n {path}", self.workflow)
        for module in (
            "tests.test_hostpanel_build",
            "tests.test_custombuild_openlitespeed",
            "tests.test_custombuild_ssl",
            "tests.test_custombuild_review_fixes",
            "tests.test_openlitespeed_fallback",
        ):
            self.assertIn(module, self.workflow)
        self.assertIn("tools/hostpanel_build_ssl.py", self.workflow)
        self.assertRegex(
            self.workflow,
            re.compile(r"shellcheck .*auto-install\.sh .*quick-install\.sh .*install-one-line\.sh", re.DOTALL),
        )


if __name__ == "__main__":
    unittest.main()
