from __future__ import annotations

import importlib.util
import io
import json
import pathlib
import sys
import tarfile
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
UPDATER_PATH = ROOT / "tools" / "hostpanel-update.py"
BUILDER_PATH = ROOT / "tools" / "build-update-release.py"
HARDENER_PATH = ROOT / "tools" / "harden_install.py"
INSTALL_AGENT = ROOT / "tools" / "install-update-agent.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "publish-release.yml"
SERVICE = ROOT / "packaging" / "systemd" / "hostpanel-update.service"
TIMER = ROOT / "packaging" / "systemd" / "hostpanel-update.timer"
PUBLIC_KEY = ROOT / "releases" / "update.pub"


def load_updater():
    spec = importlib.util.spec_from_file_location("hostpanel_update", UPDATER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load HostPanel updater")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class GitHubUpdatePipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.updater = load_updater()
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")
        cls.install_agent = INSTALL_AGENT.read_text(encoding="utf-8")
        cls.service = SERVICE.read_text(encoding="utf-8")
        cls.timer = TIMER.read_text(encoding="utf-8")
        cls.hardener = HARDENER_PATH.read_text(encoding="utf-8")

    def test_release_workflow_publishes_new_signed_source_versions(self) -> None:
        self.assertIn("branches: [main]", self.workflow)
        self.assertIn("hostpanel-v*-source.tar.gz", self.workflow)
        self.assertIn("hostpanel-v*-source.tar.gz.sig", self.workflow)
        self.assertIn("SHA256SUMS", self.workflow)
        self.assertIn("permissions:\n  contents: write", self.workflow)
        self.assertIn(
            'gh api "repos/$GITHUB_REPOSITORY/git/ref/tags/$TAG"',
            self.workflow,
        )
        self.assertIn("tools/build-update-release.py", self.workflow)
        self.assertIn("HOSTPANEL_RELEASE_PRIVATE_KEY", self.workflow)
        self.assertIn("openssl pkeyutl -sign", self.workflow)
        self.assertIn(
            "openssl pkeyutl -verify -pubin -inkey releases/update.pub",
            self.workflow,
        )
        self.assertIn("gh release create", self.workflow)
        self.assertNotIn("BEGIN PRIVATE KEY", self.workflow)

    def test_update_agent_is_installed_and_polled_frequently(self) -> None:
        self.assertIn("/opt/hostpanel/tools/hostpanel-update", self.install_agent)
        self.assertIn("HP_UPDATE_REPOSITORY=1-vps/hostpanel", self.install_agent)
        self.assertIn("HP_AUTO_UPDATE=yes", self.install_agent)
        self.assertIn("HP_UPDATE_REQUIRE_TOKEN=yes", self.install_agent)
        self.assertIn("chmod 600", self.install_agent)
        self.assertIn("systemctl enable --now hostpanel-update.timer", self.install_agent)
        self.assertIn(
            "ExecStart=/opt/hostpanel/tools/hostpanel-update --apply --auto",
            self.service,
        )
        self.assertIn("OnUnitActiveSec=5min", self.timer)
        self.assertIn("Persistent=true", self.timer)

    def test_generated_installer_enables_agent_after_health_checks(self) -> None:
        self.assertIn("signed GitHub update agent installation", self.hardener)
        self.assertIn("EXPECTED_UPDATE_AGENT_BLOBS", self.hardener)
        self.assertIn(
            "hostpanel-bootstrap.*/repository/tools/install-update-agent.sh",
            self.hardener,
        )
        self.assertIn("git hash-object --no-filters", self.hardener)
        self.assertIn(
            'bash "$UPDATE_AGENT_ROOT/tools/install-update-agent.sh" >>"$LOG" 2>&1',
            self.hardener,
        )
        self.assertIn('for backup in "${TREE_ROLLBACK_BACKUPS[@]}"; do', self.hardener)

    def test_public_key_is_ed25519_pem(self) -> None:
        text = PUBLIC_KEY.read_text(encoding="utf-8")
        self.assertIn("-----BEGIN PUBLIC KEY-----", text)
        self.assertIn(
            "MCowBQYDK2VwAyEAJonL5vK2NRcFkXvKZUs64ISOs+FfhwL8gQVmFO4C0qk=",
            text,
        )
        self.assertNotIn("PRIVATE", text)

    def test_semantic_version_ordering(self) -> None:
        version = self.updater.Version.parse
        self.assertLess(version("3.4.0"), version("3.4.1"))
        self.assertLess(version("3.4.1-beta.1"), version("3.4.1"))
        self.assertLess(version("3.4.1-beta.1"), version("3.4.1-beta.2"))
        self.assertFalse(version("3.4.1") < version("3.4.1"))

    def test_manifest_shape_is_fail_closed(self) -> None:
        valid = {
            "schema": 1,
            "product": "hostpanel",
            "channel": "stable",
            "version": "3.4.1",
            "commit": "a" * 40,
            "tag": "v3.4.1",
            "archive": {
                "name": "hostpanel-v3.4.1-update.tar.gz",
                "sha256": "b" * 64,
                "signature": "hostpanel-v3.4.1-update.tar.gz.sig",
            },
        }
        parsed = self.updater.ReleaseManifest.from_bytes(
            (json.dumps(valid, sort_keys=True) + "\n").encode()
        )
        self.assertEqual(parsed.version, "3.4.1")
        invalid = dict(valid)
        invalid["unexpected"] = True
        with self.assertRaises(self.updater.UpdateError):
            self.updater.ReleaseManifest.from_bytes(json.dumps(invalid).encode())
        invalid = json.loads(json.dumps(valid))
        invalid["archive"]["name"] = "../escape.tar.gz"
        with self.assertRaises(self.updater.UpdateError):
            self.updater.ReleaseManifest.from_bytes(json.dumps(invalid).encode())

    def test_archive_extraction_rejects_links_and_path_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            temporary = pathlib.Path(temporary_name)
            for member in ("link", "escape"):
                archive_path = temporary / f"{member}.tar.gz"
                with tarfile.open(archive_path, "w:gz") as archive:
                    if member == "link":
                        info = tarfile.TarInfo("hostpanel-v3.4.1/link")
                        info.type = tarfile.SYMTYPE
                        info.linkname = "/etc/passwd"
                    else:
                        info = tarfile.TarInfo("../escape")
                        info.size = 1
                    archive.addfile(info, io.BytesIO(b"x") if info.size else None)
                destination = temporary / f"out-{member}"
                destination.mkdir()
                with self.assertRaises(self.updater.UpdateError):
                    self.updater.safe_extract(
                        archive_path, destination, "hostpanel-v3.4.1"
                    )

    def test_source_files_compile_or_parse(self) -> None:
        compile(UPDATER_PATH.read_text(encoding="utf-8"), str(UPDATER_PATH), "exec")
        compile(BUILDER_PATH.read_text(encoding="utf-8"), str(BUILDER_PATH), "exec")
        compile(HARDENER_PATH.read_text(encoding="utf-8"), str(HARDENER_PATH), "exec")
        self.assertTrue(
            INSTALL_AGENT.read_text(encoding="utf-8").startswith("#!/usr/bin/env bash")
        )


if __name__ == "__main__":
    unittest.main()
