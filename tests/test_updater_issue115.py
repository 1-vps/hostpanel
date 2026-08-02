from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import pathlib
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
UPDATER_PATH = ROOT / "tools" / "hostpanel-update.py"
BUILDER_PATH = ROOT / "tools" / "build-update-release.py"
SERVICE_PATH = ROOT / "packaging" / "systemd" / "hostpanel-update.service"
INSTALL_AGENT_PATH = ROOT / "tools" / "install-update-agent.sh"
KEYRING_PATH = ROOT / "releases" / "update-keyring.json"
PUBLIC_KEY_PATH = ROOT / "releases" / "update.pub"
RELEASE_VERSION_PATH = ROOT / "RELEASE_VERSION"


def load(path: pathlib.Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class Issue115UpdaterHardeningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.updater = load(UPDATER_PATH, "issue115_updater")
        cls.builder = load(BUILDER_PATH, "issue115_builder")
        cls.updater_source = UPDATER_PATH.read_text(encoding="utf-8")
        cls.service = SERVICE_PATH.read_text(encoding="utf-8")
        cls.install_agent = INSTALL_AGENT_PATH.read_text(encoding="utf-8")

    def private_root(self, name: str) -> pathlib.Path:
        root = pathlib.Path(name)
        os.chmod(root, 0o700)
        return root

    def test_strict_bounded_semver(self) -> None:
        version = self.updater.Version.parse
        self.assertLess(version("3.4.1-beta.9"), version("3.4.1-beta.10"))
        self.assertLess(version("3.4.1-beta.10"), version("3.4.1"))
        for invalid in (
            "3.4.1-beta.01",
            "3.4.1+build",
            "03.4.1",
            "3.4.1-" + "9" * 65,
            "3.4.1-" + ".".join(["x"] * 33),
        ):
            with self.assertRaises(self.updater.UpdateError, msg=invalid):
                version(invalid)

    def test_channel_and_version_forms_are_bound(self) -> None:
        base = {
            "schema": 1,
            "product": "hostpanel",
            "commit": "a" * 40,
            "archive": {
                "name": "hostpanel-v3.4.1-update.tar.gz",
                "sha256": "b" * 64,
                "signature": "hostpanel-v3.4.1-update.tar.gz.sig",
            },
        }
        invalid_stable = dict(base, channel="stable", version="3.4.1-beta.1", tag="v3.4.1-beta.1")
        with self.assertRaisesRegex(self.updater.UpdateError, "stable manifests"):
            self.updater.ReleaseManifest.from_bytes(json.dumps(invalid_stable).encode())
        invalid_beta = dict(base, channel="beta", version="3.4.1", tag="v3.4.1")
        with self.assertRaisesRegex(self.updater.UpdateError, "beta manifests"):
            self.updater.ReleaseManifest.from_bytes(json.dumps(invalid_beta).encode())

    def test_config_and_token_are_descriptor_bound_and_private(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = self.private_root(temporary_name)
            config = root / "update-agent.conf"
            config.write_text("HP_UPDATE_CHANNEL=stable\n", encoding="utf-8")
            config.chmod(0o600)
            self.assertEqual(
                self.updater.load_config([config])["HP_UPDATE_CHANNEL"], "stable"
            )
            config.chmod(0o640)
            with self.assertRaisesRegex(self.updater.UpdateError, "mode 0600"):
                self.updater.load_config([config])
            config.chmod(0o600)
            link = root / "config-link"
            link.symlink_to(config.name)
            with self.assertRaises(self.updater.UpdateError):
                self.updater.load_config([link])
            hardlink = root / "config-hardlink"
            os.link(config, hardlink)
            with self.assertRaises(self.updater.UpdateError):
                self.updater.load_config([config])

        with tempfile.TemporaryDirectory() as temporary_name:
            root = self.private_root(temporary_name)
            token = root / "token"
            token.write_bytes(b"github_pat_valid")
            token.chmod(0o600)
            self.assertEqual(self.updater.token_from_file(token), "github_pat_valid")
            for payload in (b"github_pat_valid\n", "tøken".encode()):
                token.write_bytes(payload)
                with self.assertRaises(self.updater.UpdateError):
                    self.updater.token_from_file(token)

    def test_config_rejects_non_allowlisted_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = self.private_root(temporary_name)
            config = root / "update-agent.conf"
            config.write_text("PYTHONPATH=/tmp/attack\n", encoding="utf-8")
            config.chmod(0o600)
            with self.assertRaisesRegex(self.updater.UpdateError, "unsupported"):
                self.updater.load_config([config])

    def test_keyring_is_bounded_and_version_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = self.private_root(temporary_name)
            key = root / "update.pub"
            key.write_bytes(PUBLIC_KEY_PATH.read_bytes())
            key.chmod(0o644)
            key_id = "sha256:" + hashlib.sha256(key.read_bytes()).hexdigest()
            keyring = root / "update-keyring.json"
            keyring.write_text(
                json.dumps(
                    {
                        "schema": 1,
                        "keys": [
                            {
                                "id": key_id,
                                "file": key.name,
                                "activate_from": "3.4.1",
                                "retire_after": "3.4.2",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            keyring.chmod(0o600)
            loaded = self.updater.load_keyring(keyring)
            self.assertFalse(loaded[0].accepts(self.updater.Version.parse("3.4.0")))
            self.assertTrue(loaded[0].accepts(self.updater.Version.parse("3.4.1")))
            self.assertTrue(loaded[0].accepts(self.updater.Version.parse("3.4.2")))
            self.assertFalse(loaded[0].accepts(self.updater.Version.parse("3.4.3")))
            key.write_bytes(b"replacement")
            with self.assertRaisesRegex(self.updater.UpdateError, "does not match"):
                self.updater.load_keyring(keyring)

    def test_repository_keyring_matches_current_public_key(self) -> None:
        data = json.loads(KEYRING_PATH.read_text(encoding="utf-8"))
        self.assertEqual(data["schema"], 1)
        self.assertEqual(len(data["keys"]), 1)
        expected = "sha256:" + hashlib.sha256(PUBLIC_KEY_PATH.read_bytes()).hexdigest()
        self.assertEqual(data["keys"][0]["id"], expected)
        self.assertEqual(data["keys"][0]["file"], "update.pub")

    def test_redirects_are_https_and_github_scoped(self) -> None:
        for url in (
            "http://api.github.com/repos/1-vps/hostpanel/releases/latest",
            "https://example.com/release",
            "https://user:password@api.github.com/release",
        ):
            with self.assertRaises(self.updater.UpdateError):
                self.updater._validate_github_url(url)
        self.updater._validate_github_url(
            "https://release-assets.githubusercontent.com/github-production-release-asset/x"
        )

    def test_duplicate_assets_and_length_mismatch_fail_closed(self) -> None:
        duplicate = {
            "assets": [
                {"name": "x", "url": "https://api.github.com/a", "size": 1},
                {"name": "x", "url": "https://api.github.com/b", "size": 1},
            ]
        }
        with self.assertRaisesRegex(self.updater.UpdateError, "duplicate"):
            self.updater.asset_map(duplicate)

        class FakeOpener:
            def open(self, request, timeout=30):
                return io.BytesIO(b"abc")

        with tempfile.TemporaryDirectory() as temporary_name:
            destination = pathlib.Path(temporary_name) / "asset"
            with self.assertRaisesRegex(self.updater.UpdateError, "size mismatch"):
                self.updater.download_asset(
                    FakeOpener(),
                    {
                        "url": "https://api.github.com/repos/1-vps/hostpanel/releases/assets/1",
                        "size": 4,
                    },
                    destination,
                    token=None,
                    maximum=10,
                )

    def test_public_key_is_captured_before_openssl(self) -> None:
        self.assertIn('TemporaryDirectory(prefix="hostpanel-key.")', self.updater_source)
        self.assertIn('key_path = pathlib.Path(temporary_name) / "captured.pub"', self.updater_source)
        self.assertNotIn(
            '"-inkey",\n            str(public_key)',
            self.updater_source,
        )

    def test_beta_uses_bounded_release_list(self) -> None:
        self.assertIn('f"?per_page={MAX_BETA_RELEASES}"', self.updater_source)
        self.assertIn("MAX_BETA_RELEASES = 20", self.updater_source)
        self.assertIn("resolved.sort(key=lambda item: Version.parse", self.updater_source)

    def test_dry_run_does_not_require_apply(self) -> None:
        args = self.updater.parse_args(["--dry-run"])
        self.assertTrue(args.dry_run)
        self.assertFalse(args.apply)
        self.assertIn("not args.apply and not args.dry_run", self.updater_source)

    def test_systemd_does_not_import_config_as_environment(self) -> None:
        self.assertNotIn("EnvironmentFile=", self.service)
        self.assertNotIn("ExecStartPre=", self.service)
        self.assertIn("SuccessExitStatus=10 75", self.service)
        self.assertIn("ConditionPathExists=/etc/hostpanel/update-keyring.json", self.service)

    def test_installer_deploys_keyring_and_config_reference(self) -> None:
        self.assertIn("releases/update-keyring.json", self.install_agent)
        self.assertIn("HP_UPDATE_KEYRING=/etc/hostpanel/update-keyring.json", self.install_agent)
        self.assertIn('install -o root -g root -m 600 "$KEYRING"', self.install_agent)

    def test_release_version_is_monotonic_and_stable(self) -> None:
        self.assertEqual(RELEASE_VERSION_PATH.read_text(encoding="utf-8").strip(), "3.4.1")
        with tempfile.TemporaryDirectory() as temporary_name:
            root = pathlib.Path(temporary_name)
            (root / "RELEASE_VERSION").write_text("3.4.1\n", encoding="utf-8")
            self.assertEqual(
                self.builder.deployable_release_version(root, "3.4.0", "stable"),
                "3.4.1",
            )
            with self.assertRaisesRegex(SystemExit, "must be greater"):
                self.builder.deployable_release_version(root, "3.4.1", "stable")


if __name__ == "__main__":
    unittest.main()
