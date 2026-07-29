import pathlib
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
BASE = ROOT / "install.base.sh"
HARDENER = ROOT / "tools" / "harden_install.py"


class SystemdResolvedDnsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as directory:
            output = pathlib.Path(directory) / "install.generated.sh"
            subprocess.run(
                [sys.executable, str(HARDENER), str(BASE), str(output)],
                check=True,
                cwd=ROOT,
            )
            cls.generated = output.read_text(encoding="utf-8")

    def test_only_standard_loopback_stub_is_allowed(self):
        self.assertIn("systemd-resolved preflight allowance", HARDENER.read_text(encoding="utf-8"))
        self.assertIn("! grep -Fvq 'systemd-resolve'", self.generated)
        self.assertIn(
            "127\\.0\\.0\\.(53|54)(%lo)?:53([[:space:]]|$)",
            self.generated,
        )
        self.assertIn(
            "Port $port is already in use. Use a clean server",
            self.generated,
        )

    def test_dns_role_disables_stub_but_retains_upstream_resolver(self):
        self.assertIn(
            "/etc/systemd/resolved.conf.d/hostpanel-dns.conf",
            self.generated,
        )
        self.assertIn("DNSStubListener=no", self.generated)
        self.assertIn(
            "ln -sfn /run/systemd/resolve/resolv.conf /etc/resolv.conf",
            self.generated,
        )
        self.assertIn("systemctl restart systemd-resolved", self.generated)
        self.assertIn(
            "systemd-resolved still owns port 53 after disabling its DNS stub",
            self.generated,
        )

    def test_non_dns_roles_do_not_run_resolved_mutation(self):
        self.assertIn(
            'if has_role dns && [[ "$PKG_FAMILY" == debian ]]',
            self.generated,
        )


if __name__ == "__main__":
    unittest.main()
