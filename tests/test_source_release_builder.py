from __future__ import annotations

import importlib.util
import io
import json
import pathlib
import stat
import sys
import tarfile
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "build-source-release.py"


def load_module():
    spec = importlib.util.spec_from_file_location("source_release_builder_test", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load source release builder")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class SourceReleaseBuilderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.builder = load_module()

    def test_release_identity_requires_matching_core_versions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = pathlib.Path(temporary_name)
            (root / "SOURCE_VERSION").write_text("3.4.1\n", encoding="utf-8")
            (root / "RELEASE_VERSION").write_text(
                "3.4.1-hardened-r1\n", encoding="utf-8"
            )
            identity = self.builder.load_identity(root)
            self.assertEqual(identity.source_version, "3.4.1")
            self.assertEqual(identity.release_id, "3.4.1-hardened-r1")
            self.assertEqual(
                identity.archive_name,
                "hostpanel-v3.4.1-hardened-r1-source.tar.gz",
            )
            (root / "SOURCE_VERSION").write_text("3.4.2\n", encoding="utf-8")
            with self.assertRaisesRegex(
                self.builder.ReleaseBuildError,
                "same core version",
            ):
                self.builder.load_identity(root)

            (root / "SOURCE_VERSION").write_text(" 3.4.1\n", encoding="utf-8")
            with self.assertRaisesRegex(
                self.builder.ReleaseBuildError,
                "clean non-empty line",
            ):
                self.builder.load_identity(root)

    def test_policy_is_strict_sorted_and_rejects_unsafe_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            path = pathlib.Path(temporary_name) / "policy.json"
            payload = {
                "schema": 1,
                "overlay_paths": ["app", "tools"],
                "required_paths": ["VERSION", "app/templates/panel.html"],
            }
            path.write_text(json.dumps(payload), encoding="utf-8")
            policy = self.builder.load_policy(path)
            self.assertEqual(policy.overlay_paths, ("app", "tools"))

            payload["overlay_paths"] = ["tools", "app"]
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(self.builder.ReleaseBuildError, "sorted"):
                self.builder.load_policy(path)

            payload["overlay_paths"] = ["../escape"]
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(self.builder.ReleaseBuildError):
                self.builder.load_policy(path)

    def test_safe_extraction_rejects_links_and_path_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = pathlib.Path(temporary_name)
            for name, configure in (
                ("escape.tar", lambda info: setattr(info, "name", "../escape")),
                ("link.tar", lambda info: setattr(info, "type", tarfile.SYMTYPE)),
            ):
                archive_path = root / name
                with tarfile.open(archive_path, "w") as archive:
                    info = tarfile.TarInfo("root/file")
                    info.size = 0
                    configure(info)
                    archive.addfile(info, io.BytesIO(b""))
                with self.assertRaises(self.builder.ReleaseBuildError):
                    self.builder.extract_tar_safely(
                        archive_path,
                        root / f"extract-{name}",
                        require_single_root=False,
                    )

    def test_deterministic_archive_has_canonical_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = pathlib.Path(temporary_name)
            source = root / "source"
            (source / "app").mkdir(parents=True)
            (source / "VERSION").write_text("3.4.1\n", encoding="utf-8")
            (source / "app" / "entry.py").write_text(
                "print('ok')\n", encoding="utf-8"
            )
            executable = source / "install.sh"
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o755)
            identity = self.builder.ReleaseIdentity(
                source_version="3.4.1",
                release_id="3.4.1-hardened-r1",
            )
            timestamp = 1_700_000_000
            first = root / "first.tar.gz"
            second = root / "second.tar.gz"
            self.builder.write_deterministic_archive(
                source, first, identity, timestamp
            )
            self.builder.write_deterministic_archive(
                source, second, identity, timestamp
            )
            self.assertEqual(first.read_bytes(), second.read_bytes())
            with tarfile.open(first, "r:gz") as archive:
                members = archive.getmembers()
                self.assertTrue(members)
                for member in members:
                    self.assertEqual((member.uid, member.gid), (0, 0))
                    self.assertEqual((member.uname, member.gname), ("root", "root"))
                    self.assertEqual(int(member.mtime), timestamp)
                    self.assertFalse(member.issym() or member.islnk())
                install = archive.getmember(
                    "hostpanel-v3.4.1-hardened-r1/install.sh"
                )
                regular = archive.getmember(
                    "hostpanel-v3.4.1-hardened-r1/app/entry.py"
                )
                self.assertEqual(stat.S_IMODE(install.mode), 0o755)
                self.assertEqual(stat.S_IMODE(regular.mode), 0o644)

    def test_built_archive_validation_binds_version_and_required_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = pathlib.Path(temporary_name)
            source = root / "source"
            (source / "app" / "templates").mkdir(parents=True)
            (source / "VERSION").write_text("3.4.1\n", encoding="utf-8")
            (source / "app" / "templates" / "panel.html").write_text(
                "panel\n", encoding="utf-8"
            )
            identity = self.builder.ReleaseIdentity("3.4.1", "3.4.1-hardened-r1")
            policy = self.builder.SourcePolicy(
                overlay_paths=("app",),
                required_paths=("VERSION", "app/templates/panel.html"),
            )
            timestamp = 1_700_000_000
            archive_path = root / identity.archive_name
            self.builder.write_deterministic_archive(
                source, archive_path, identity, timestamp
            )
            self.builder.validate_built_archive(
                archive_path, identity, policy, timestamp
            )

            (source / "VERSION").write_text("3.4.0\n", encoding="utf-8")
            mismatched = root / "mismatched.tar.gz"
            self.builder.write_deterministic_archive(
                source, mismatched, identity, timestamp
            )
            with self.assertRaisesRegex(
                self.builder.ReleaseBuildError,
                "VERSION does not match",
            ):
                self.builder.validate_built_archive(
                    mismatched, identity, policy, timestamp
                )

    def test_post_processing_uses_the_git_checkout_for_reviewed_localization(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = pathlib.Path(temporary_name)
            source = root / "source"
            repository = root / "repository"
            source.mkdir()
            repository.mkdir()
            commands: list[list[str]] = []
            original_run = self.builder.run

            def record(arguments, **_kwargs):
                commands.append(list(arguments))
                return type("Completed", (), {"stdout": ""})()

            self.builder.run = record
            try:
                self.builder.run_post_processing(
                    source,
                    repository,
                    self.builder.ReleaseIdentity("3.4.1", "3.4.1-hardened-r1"),
                )
            finally:
                self.builder.run = original_run

            localization_command = commands[1]
            self.assertEqual(
                pathlib.Path(localization_command[1]),
                repository / "localization-overlay" / "apply_localization_overlay_reviewed.py",
            )
            self.assertEqual(pathlib.Path(localization_command[2]), source)
            self.assertEqual(
                pathlib.Path(localization_command[3]),
                repository / "localization-overlay",
            )

    def test_source_compiles(self) -> None:
        compile(MODULE_PATH.read_text(encoding="utf-8"), str(MODULE_PATH), "exec")


if __name__ == "__main__":
    unittest.main()
