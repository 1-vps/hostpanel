import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
HARNESS = ROOT / "tools" / "run-qemu-vm-acceptance.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "qemu-vm-acceptance.yml"


class QemuDefaultVersionTests(unittest.TestCase):
    def test_local_harness_default_matches_ci_release_version(self):
        harness = HARNESS.read_text(encoding="utf-8")
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn(
            'EXPECTED_VERSION="${HP_QEMU_EXPECTED_VERSION:-3.4.0}"',
            harness,
        )
        self.assertIn("HP_QEMU_EXPECTED_VERSION: 3.4.0", workflow)
        self.assertNotIn("3.4.0-hardened-r6", harness)

    def test_success_evidence_omits_generic_unredacted_journals(self):
        harness = HARNESS.read_text(encoding="utf-8")
        self.assertNotIn("guest-failure-diagnostics.txt", harness)
        self.assertNotIn("journalctl -b -p warning..alert", harness)
        self.assertIn("extract_guest_evidence", harness)

    def test_artifact_cleanup_rejects_symlinked_directories(self):
        harness = HARNESS.read_text(encoding="utf-8")
        self.assertIn('ARTIFACT_ROOT="$REPO_ROOT/artifacts"', harness)
        self.assertIn('for artifact_path in "$ARTIFACT_ROOT" "$ARTIFACT_DIR"', harness)
        self.assertIn('[[ -d "$artifact_path" && ! -L "$artifact_path" ]]', harness)
        self.assertIn("artifact directories changed during setup", harness)
        self.assertIn(
            'find "$ARTIFACT_DIR" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +',
            harness,
        )
        self.assertNotIn('rm -rf "$ARTIFACT_DIR"/*', harness)

    def test_work_directory_is_private_and_unique_per_run(self):
        harness = HARNESS.read_text(encoding="utf-8")
        self.assertIn('WORK_PARENT="${RUNNER_TEMP:-/tmp}"', harness)
        self.assertIn('WORK_DIR="$(mktemp -d "$WORK_PARENT/hostpanel-qemu-acceptance.XXXXXX")"', harness)
        self.assertIn('[[ -d "$WORK_PARENT" && ! -L "$WORK_PARENT" ]]', harness)
        self.assertIn('if ! qemu_pid_is_ours && [[ -n "$WORK_DIR"', harness)
        self.assertNotIn('WORK_DIR="${RUNNER_TEMP:-/tmp}/hostpanel-qemu-acceptance"', harness)

    def test_all_forwarded_host_ports_are_unique_and_available(self):
        harness = HARNESS.read_text(encoding="utf-8")
        self.assertIn("HOST_FORWARD_PORTS=(", harness)
        for port in ("30025", "30143", "30993", "30080", "30443"):
            self.assertIn(port, harness)
        self.assertIn('python3 - "${HOST_FORWARD_PORTS[@]}"', harness)
        self.assertIn('sock.bind(("127.0.0.1", port))', harness)
        self.assertIn("duplicate QEMU host-forward port", harness)
        self.assertIn("must be unique and free on 127.0.0.1", harness)


if __name__ == "__main__":
    unittest.main()
