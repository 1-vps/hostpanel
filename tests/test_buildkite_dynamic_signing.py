from __future__ import annotations

import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONFIGURE = ROOT / ".buildkite/agent/configure-agent.sh"
UPLOAD = ROOT / ".buildkite/agent/upload-trusted-pipeline.sh"
PIPELINE = ROOT / ".buildkite/pipeline.yml"
CONTRACT = ROOT / ".buildkite/scripts/run-pipeline-contract.sh"
CODEOWNERS = ROOT / ".github/CODEOWNERS"


class BuildkiteDynamicSigningTests(unittest.TestCase):
    def test_only_upload_queue_receives_private_signing_material(self) -> None:
        text = CONFIGURE.read_text(encoding="utf-8")
        self.assertIn('if [[ "$queue" == "hostpanel-upload" ]]; then', text)
        self.assertIn('fail "the upload queue requires --signing-jwks"', text)
        self.assertIn('fail "the upload queue requires a safe --signing-key-id"', text)
        self.assertIn('fail "runner queues must not receive signing key material"', text)
        self.assertIn(
            'install -o root -g buildkite-agent -m 0640 "$staged_signing_jwks" /etc/buildkite-agent/keys/signing.jwks',
            text,
        )
        self.assertIn('rm -f /etc/buildkite-agent/keys/signing.jwks', text)

    def test_upload_agent_configures_signing_file_and_verified_key_id_input(self) -> None:
        text = CONFIGURE.read_text(encoding="utf-8")
        self.assertIn('signing-jwks-file="/etc/buildkite-agent/keys/signing.jwks"', text)
        self.assertIn('signing-jwks-key-id="${signing_key_id}"', text)
        self.assertIn(
            'item.get("kid") == signing_key_id and "d" in item',
            text,
        )
        self.assertIn('verification-failure-behavior="block"', text)
        self.assertIn('verification-jwks-file="/etc/buildkite-agent/keys/verification.jwks"', text)

    def test_dynamic_pipeline_is_root_controlled_and_hash_pinned_before_upload(self) -> None:
        text = UPLOAD.read_text(encoding="utf-8")
        self.assertIn('pipeline=/etc/buildkite-agent/hostpanel-pipeline.yml', text)
        self.assertIn('digest_file=/etc/buildkite-agent/hostpanel-policy/pipeline-sha256', text)
        self.assertIn('pipeline_data = read_root_owned(sys.argv[1]', text)
        self.assertIn('digest = read_root_owned(sys.argv[2]', text)
        self.assertIn('actual = hashlib.sha256(pipeline_data).hexdigest()', text)
        self.assertIn('if actual != digest:', text)
        self.assertIn('buildkite-agent pipeline upload', text)
        self.assertIn('--no-interpolation', text)
        self.assertIn('--reject-secrets', text)
        self.assertIn('--reject-parse-warnings', text)

    def test_configure_installs_reviewed_pipeline_copy_hash_and_uploader(self) -> None:
        text = CONFIGURE.read_text(encoding="utf-8")
        self.assertIn(
            'install -o root -g buildkite-agent -m 0440 \\\n    "$script_root/.buildkite/pipeline.yml" \\\n    /etc/buildkite-agent/hostpanel-pipeline.yml',
            text,
        )
        self.assertIn('sha256sum /etc/buildkite-agent/hostpanel-pipeline.yml', text)
        self.assertIn('/etc/buildkite-agent/hostpanel-policy/pipeline-sha256', text)
        self.assertIn(
            'install -o root -g buildkite-agent -m 0550 \\\n    "$script_root/.buildkite/agent/upload-trusted-pipeline.sh" \\\n    /usr/local/libexec/hostpanel-upload-pipeline',
            text,
        )

    def test_dynamic_pipeline_contains_only_reviewed_repository_commands(self) -> None:
        text = PIPELINE.read_text(encoding="utf-8")
        self.assertIn('command: ".buildkite/scripts/run-pipeline-contract.sh"', text)
        self.assertIn('command: ".buildkite/scripts/run-check.sh"', text)
        self.assertIn('command: ".buildkite/scripts/run-qemu.sh"', text)
        self.assertNotIn('buildkite-agent pipeline upload', text)

    def test_pipeline_contract_and_codeowners_cover_signing_chain(self) -> None:
        self.assertIn(
            "tests.test_buildkite_dynamic_signing",
            CONTRACT.read_text(encoding="utf-8"),
        )
        self.assertIn(
            "/tests/test_buildkite_dynamic_signing.py @1-vps",
            CODEOWNERS.read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
