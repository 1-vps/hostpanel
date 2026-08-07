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
    def test_only_upload_queue_requires_private_signing_jwks(self) -> None:
        text = CONFIGURE.read_text(encoding="utf-8")
        self.assertIn('if [[ "$queue" == "hostpanel-upload" ]]; then', text)
        self.assertIn('[[ -n "$signing_jwks" ]] || fail "hostpanel-upload requires --signing-jwks"', text)
        self.assertIn('[[ -z "$signing_jwks" ]] || fail "runner queues must not receive --signing-jwks"', text)
        self.assertIn(
            'install -o root -g buildkite-agent -m 0640 "$staged_signing_jwks" /etc/buildkite-agent/keys/signing.jwks',
            text,
        )

    def test_upload_agent_configures_signing_file_and_key_id(self) -> None:
        text = CONFIGURE.read_text(encoding="utf-8")
        self.assertIn('signing-jwks-file="/etc/buildkite-agent/keys/signing.jwks"', text)
        self.assertIn('signing-jwks-key-id="hostpanel-2026-08"', text)
        self.assertIn('verification-failure-behavior="block"', text)
        self.assertIn('verification-jwks-file="/etc/buildkite-agent/keys/verification.jwks"', text)

    def test_dynamic_pipeline_is_pinned_before_upload(self) -> None:
        text = UPLOAD.read_text(encoding="utf-8")
        self.assertIn('pipeline="/etc/buildkite-agent/hostpanel-pipeline.yml"', text)
        self.assertIn('expected_sha_file="/etc/buildkite-agent/hostpanel-policy/pipeline-sha256"', text)
        self.assertIn('actual_sha="$(sha256sum "$pipeline" | awk', text)
        self.assertIn('[[ "$actual_sha" == "$expected_sha" ]]', text)
        self.assertIn('buildkite-agent pipeline upload', text)
        self.assertIn('--no-interpolation', text)
        self.assertIn('--reject-secrets', text)
        self.assertIn('--reject-parse-warnings', text)

    def test_configure_installs_reviewed_pipeline_copy_and_hash(self) -> None:
        text = CONFIGURE.read_text(encoding="utf-8")
        self.assertIn('install -o root -g root -m 0644 "$pipeline" /etc/buildkite-agent/hostpanel-pipeline.yml', text)
        self.assertIn('pipeline_sha="$(sha256sum "$pipeline" | awk', text)
        self.assertIn('/etc/buildkite-agent/hostpanel-policy/pipeline-sha256', text)
        self.assertIn('install -o root -g buildkite-agent -m 0750 "$uploader" /usr/local/libexec/hostpanel-upload-pipeline', text)

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
