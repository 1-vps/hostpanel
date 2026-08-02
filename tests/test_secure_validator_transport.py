from __future__ import annotations

import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
PROVIDER_WORKFLOW = ROOT / ".github" / "workflows" / "vps-acceptance.yml"
QEMU_HARNESS = ROOT / "tools" / "run-qemu-vm-acceptance.sh"
QEMU_GUEST = ROOT / "tools" / "qemu-guest-install.sh"
VALIDATOR = ROOT / "tools" / "validate-production-vm.sh"
HELPER = ROOT / "tools" / "secure-validation-io.py"


class SecureValidatorTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.provider = PROVIDER_WORKFLOW.read_text(encoding="utf-8")
        cls.harness = QEMU_HARNESS.read_text(encoding="utf-8")
        cls.guest = QEMU_GUEST.read_text(encoding="utf-8")
        cls.validator = VALIDATOR.read_text(encoding="utf-8")
        cls.helper = HELPER.read_text(encoding="utf-8")

    def test_provider_copies_validates_and_cleans_helper(self) -> None:
        self.assertIn("tools/secure-validation-io.py", self.provider)
        self.assertIn("/root/secure-validation-io.py", self.provider)
        self.assertIn("python3 -m py_compile /root/secure-validation-io.py", self.provider)
        self.assertIn("chmod 700 \\\n              /root/bootstrap-install.sh", self.provider)
        self.assertGreaterEqual(
            self.provider.count("/root/secure-validation-io.py"),
            5,
        )
        self.assertGreaterEqual(
            self.provider.count("HP_PROVIDER_INSTANCE_ID=\"$HP_PROVIDER_INSTANCE_ID\""),
            3,
        )
        self.assertGreaterEqual(
            self.provider.count("HP_PROVIDER_SNAPSHOT_ID=\"$HP_PROVIDER_SNAPSHOT_ID\""),
            3,
        )
        self.assertGreaterEqual(
            self.provider.count(
                "HP_PROVIDER_SNAPSHOT_CREATED_AT_UTC=\"$HP_PROVIDER_SNAPSHOT_CREATED_AT_UTC\""
            ),
            3,
        )

    def test_qemu_promotes_helper_from_verified_descriptor(self) -> None:
        self.assertIn('"$REPO_ROOT/tools/secure-validation-io.py"', self.harness)
        self.assertIn('"/tmp/secure-validation-io.py"), 0o700', self.harness)
        scp = self.harness.index('scp "${scp_opts[@]}"')
        helper_copy = self.harness.index(
            '"$REPO_ROOT/tools/secure-validation-io.py"',
            scp,
        )
        promotion = self.harness.index(
            '(pathlib.Path("/tmp/secure-validation-io.py"), 0o700)',
            helper_copy,
        )
        guest_start = self.harness.index("sudo /tmp/qemu-guest-install.sh", promotion)
        self.assertLess(helper_copy, promotion)
        self.assertLess(promotion, guest_start)

    def test_qemu_guest_installs_and_removes_helper_across_reboot(self) -> None:
        self.assertIn("/tmp/secure-validation-io.py", self.guest)
        self.assertIn(
            "install -o root -g root -m 700 /tmp/secure-validation-io.py /root/secure-validation-io.py",
            self.guest,
        )
        install = self.guest.index("/root/secure-validation-io.py")
        pre_reboot = self.guest.index("--prepare-reboot", install)
        cleanup = self.guest.index(
            "/root/secure-validation-io.py",
            self.guest.index("cleanup_acceptance_state(){"),
        )
        post_reboot = self.guest.index("--post-reboot", cleanup)
        self.assertLess(install, pre_reboot)
        self.assertLess(cleanup, post_reboot)

    def test_validator_uses_adjacent_single_linked_helper(self) -> None:
        self.assertIn("secure-validation-io.py", self.validator)
        self.assertIn("root-owned, single-linked", self.validator)
        self.assertIn('"$PYTHON3" -I "$VALIDATION_IO" run-with-report', self.validator)
        self.assertIn('"$PYTHON3" -I "$VALIDATION_IO" exec-hook', self.validator)
        self.assertIn('"$PYTHON3" -I "$VALIDATION_IO" read-state', self.validator)
        self.assertIn('"$PYTHON3" -I "$VALIDATION_IO" write-state', self.validator)
        self.assertIn("os.O_NOFOLLOW", self.helper)
        self.assertIn("os.execve(high_fd", self.helper)
        self.assertIn("os.replace(", self.helper)
        self.assertIn("os.fsync(parent.fd)", self.helper)

    def test_helper_never_enters_evidence_artifacts(self) -> None:
        upload = self.provider.index("      - name: Upload sealed acceptance evidence")
        self.assertNotIn("secure-validation-io.py", self.provider[upload:])
        self.assertNotIn("secure-validation-io.py", self.harness.split("extract_guest_evidence(){", 1)[0])


if __name__ == "__main__":
    unittest.main()
