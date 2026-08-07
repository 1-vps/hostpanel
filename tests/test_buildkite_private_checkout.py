from __future__ import annotations

import os
import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
HOOK = ROOT / ".buildkite/agent/checkout-private-repo"
CONFIGURE = ROOT / ".buildkite/agent/configure-agent.sh"
PIPELINE = ROOT / ".buildkite/pipeline.yml"
CONTRACT = ROOT / ".buildkite/scripts/run-pipeline-contract.sh"
CODEOWNERS = ROOT / ".github/CODEOWNERS"


class BuildkitePrivateCheckoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.git(self.source, "init", "-b", "main")
        self.git(self.source, "config", "user.name", "HostPanel Test")
        self.git(self.source, "config", "user.email", "hostpanel-test@example.invalid")
        (self.source / "README").write_text("private checkout\n", encoding="utf-8")
        self.git(self.source, "add", "README")
        self.git(self.source, "commit", "-m", "source")
        self.commit = self.git(self.source, "rev-parse", "HEAD")

        self.builds = self.root / "builds"
        self.builds.mkdir()
        self.checkout = self.builds / "job"
        self.key_dir = self.root / "credential"
        self.key_dir.mkdir(mode=0o700)
        self.key = self.key_dir / "deploy-key"
        self.key.write_text(
            "-----BEGIN OPENSSH PRIVATE KEY-----\nTEST-ONLY\n-----END OPENSSH PRIVATE KEY-----\n",
            encoding="utf-8",
        )
        self.key.chmod(0o400)
        self.known_hosts = self.root / "known_hosts"
        self.known_hosts.write_text("github.com ssh-ed25519 TEST\n", encoding="utf-8")
        self.known_hosts.chmod(0o440)

        rendered = self.root / "checkout"
        rendered_source = HOOK.read_text(encoding="utf-8")
        rendered_source = rendered_source.replace(
            "expected_repo='git@github.com:1-vps/hostpanel.git'",
            f"expected_repo={self.shell_quote(str(self.source))}",
        )
        rendered_source = rendered_source.replace(
            "key=/run/hostpanel-buildkite/checkout-deploy-key",
            f"key={self.shell_quote(str(self.key))}",
        )
        rendered_source = rendered_source.replace(
            "known_hosts=/var/lib/buildkite-agent/.ssh/known_hosts",
            f"known_hosts={self.shell_quote(str(self.known_hosts))}",
        )
        rendered_source = rendered_source.replace(
            "base = pathlib.Path('/var/lib/buildkite-agent/builds').resolve()",
            f"base = pathlib.Path({str(self.builds)!r}).resolve()",
        )
        rendered_source = rendered_source.replace(
            "(known_hosts, 0o440, 0)", "(known_hosts, 0o440, os.getuid())"
        )
        rendered_source = rendered_source.replace(
            "export GIT_ALLOW_PROTOCOL=ssh", "export GIT_ALLOW_PROTOCOL=file"
        )
        rendered.write_text(rendered_source, encoding="utf-8")
        rendered.chmod(0o755)
        self.rendered = rendered

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def shell_quote(value: str) -> str:
        return "'" + value.replace("'", "'\\''") + "'"

    @staticmethod
    def git(repository: pathlib.Path, *args: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(repository), *args],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return result.stdout.strip()

    def run_hook(self, *, commit: str | None = None) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.update(
            {
                "BUILDKITE_REPO": str(self.source),
                "BUILDKITE_COMMIT": commit or self.commit,
                "BUILDKITE_BUILD_CHECKOUT_PATH": str(self.checkout),
            }
        )
        return subprocess.run(
            [str(self.rendered)],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )

    def test_exact_checkout_succeeds_and_destroys_key(self) -> None:
        completed = self.run_hook()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("credential destroyed", completed.stdout)
        self.assertFalse(self.key.exists())
        self.assertEqual(self.git(self.checkout, "rev-parse", "HEAD"), self.commit)
        self.assertEqual(self.git(self.checkout, "remote", "get-url", "origin"), str(self.source))
        self.assertEqual(self.git(self.checkout, "status", "--porcelain=v1", "--untracked-files=all"), "")

    def test_invalid_commit_fails_and_still_destroys_key(self) -> None:
        completed = self.run_hook(commit="not-a-sha")
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("BUILDKITE_COMMIT", completed.stderr)
        self.assertFalse(self.key.exists())

    def test_nonempty_checkout_fails_and_destroys_key(self) -> None:
        self.checkout.mkdir()
        (self.checkout / "unexpected").write_text("x", encoding="utf-8")
        completed = self.run_hook()
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("not empty", completed.stderr)
        self.assertFalse(self.key.exists())

    def test_checkout_key_never_uses_buildkite_secrets_or_artifacts(self) -> None:
        hook = HOOK.read_text(encoding="utf-8")
        pipeline = PIPELINE.read_text(encoding="utf-8")
        configure = CONFIGURE.read_text(encoding="utf-8")
        self.assertIn("/run/hostpanel-buildkite/checkout-deploy-key", hook)
        self.assertIn("StrictHostKeyChecking=yes", hook)
        self.assertIn("core.hooksPath=/dev/null", hook)
        self.assertIn("GIT_CONFIG_COUNT", hook)
        self.assertIn("GIT_CONFIG_KEY_*|GIT_CONFIG_VALUE_*", hook)
        self.assertIn("/usr/bin/timeout --signal=TERM --kill-after=10s 600", hook)
        self.assertIn("exact commit fetch failed after three bounded attempts", hook)
        self.assertIn("cleanup_checkout_key", hook)
        self.assertIn("key_data = read_checked(key, 0o400, os.getuid())", hook)
        self.assertIn("key_data.startswith", hook)
        self.assertNotIn("key.read_bytes()", hook)
        for forbidden in ("secret get", "artifact download", "HP_GIT_SSH_KEY", "ssh_secret"):
            self.assertNotIn(forbidden, hook)
            self.assertNotIn(forbidden, pipeline)
        self.assertIn("runner queues require a local --checkout-key", configure)
        self.assertIn("--checkout-key must reside on tmpfs", configure)
        self.assertIn("runner workers must have swap disabled", configure)
        self.assertIn("-o buildkite-agent -g buildkite-agent -m 0400", configure)
        self.assertIn("upload queue must not receive --checkout-key", configure)
        self.assertIn('rm -f -- "$checkout_key"', configure)
        self.assertIn("consumed --checkout-key still exists", configure)

    def test_contract_and_codeowners_cover_private_checkout(self) -> None:
        self.assertIn(
            "tests.test_buildkite_private_checkout",
            CONTRACT.read_text(encoding="utf-8"),
        )
        self.assertIn(
            "/tests/test_buildkite_private_checkout.py @1-vps",
            CODEOWNERS.read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
