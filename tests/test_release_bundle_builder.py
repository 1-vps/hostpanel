from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import re
import sys
import tempfile
import types
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
BUNDLE_PATH = ROOT / "tools" / "build-release-bundle.py"


def load_module():
    spec = importlib.util.spec_from_file_location("release_bundle_builder_test", BUNDLE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load release bundle builder")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ReleaseBundleBuilderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bundle = load_module()

    def fake_modules(self, calls: list[tuple]):
        identity = types.SimpleNamespace(
            source_version="3.4.1",
            release_id="3.4.1-hardened-r1",
        )
        update_builder = types.SimpleNamespace(
            release_identity=lambda _root: identity,
            run_git=lambda _root, *_args: "a" * 40,
            COMMIT_RE=re.compile(r"^[0-9a-f]{40}$"),
        )

        def source_verify(*args):
            calls.append(("source_verify",) + args)

        def update_verify(*args):
            calls.append(("update_verify",) + args)

        source_verifier = types.SimpleNamespace(verify=source_verify)
        update_verifier = types.SimpleNamespace(verify=update_verify)
        return update_builder, source_verifier, update_verifier

    def install_fakes(self, calls: list[tuple]):
        update_builder, source_verifier, update_verifier = self.fake_modules(calls)
        original_load = self.bundle.load_module
        original_run = self.bundle.run

        def load(_name, path):
            if path.name == "build-update-release.py":
                return update_builder
            if path.name == "verify-source-release-bundle.py":
                return source_verifier
            if path.name == "verify-existing-release.py":
                return update_verifier
            raise AssertionError(path)

        def run(arguments, **_kwargs):
            args = list(arguments)
            output = pathlib.Path(args[args.index("--output-dir") + 1])
            output.mkdir(parents=True, exist_ok=True)
            if args[1].endswith("run-source-release-builder.py"):
                files = {
                    "hostpanel-v3.4.1-hardened-r1-source.tar.gz": b"source-archive",
                    "source-build.json": b'{"source":"build"}\n',
                    "sbom.cdx.json": b'{"bomFormat":"CycloneDX"}\n',
                    "dependency-lock-attestation.json": b'{"lock":true}\n',
                    "source-file-manifest.json": b'{"entries":[]}\n',
                }
            elif args[1].endswith("build-update-release.py"):
                files = {
                    "hostpanel-v3.4.1-update.tar.gz": b"update-archive",
                    "hostpanel-update-manifest.json": b'{"manifest":true}\n',
                    "release-build.json": b'{"release":"build"}\n',
                }
            else:
                raise AssertionError(args)
            for name, content in files.items():
                (output / name).write_bytes(content)
            calls.append(("run", pathlib.Path(args[1]).name))

        self.bundle.load_module = load
        self.bundle.run = run
        return original_load, original_run

    def restore_fakes(self, originals):
        self.bundle.load_module, self.bundle.run = originals

    def test_build_produces_exact_nine_payloads_and_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = pathlib.Path(temporary_name)
            output = root / "dist"
            calls: list[tuple] = []
            originals = self.install_fakes(calls)
            try:
                summary = self.bundle.build(ROOT, "a" * 40, "stable", output)
            finally:
                self.restore_fakes(originals)

            expected = {
                "hostpanel-v3.4.1-hardened-r1-source.tar.gz",
                "source-build.json",
                "sbom.cdx.json",
                "dependency-lock-attestation.json",
                "source-file-manifest.json",
                "SOURCE-CANDIDATE-SHA256SUMS",
                "hostpanel-v3.4.1-update.tar.gz",
                "hostpanel-update-manifest.json",
                "release-build.json",
            }
            self.assertEqual({path.name for path in output.iterdir()}, expected)
            self.assertEqual(set(summary["payloads"]), expected)
            self.assertEqual(summary["version"], "3.4.1")
            self.assertEqual(summary["release_id"], "3.4.1-hardened-r1")
            self.assertEqual(summary["commit"], "a" * 40)
            self.assertEqual(
                [call[0] for call in calls],
                ["run", "source_verify", "run", "update_verify"],
            )

    def test_bundle_summary_is_reproducible(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = pathlib.Path(temporary_name)
            summaries = []
            calls: list[tuple] = []
            originals = self.install_fakes(calls)
            try:
                for name in ("first", "second"):
                    summary = self.bundle.build(ROOT, "a" * 40, "stable", root / name)
                    summaries.append(
                        json.dumps(summary, sort_keys=True, separators=(",", ":"))
                    )
            finally:
                self.restore_fakes(originals)
            self.assertEqual(summaries[0], summaries[1])

    def test_nonempty_output_directory_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            output = pathlib.Path(temporary_name) / "dist"
            output.mkdir()
            (output / "existing").write_text("occupied", encoding="utf-8")
            with self.assertRaisesRegex(
                self.bundle.ReleaseBundleError,
                "must be empty",
            ):
                self.bundle.build(ROOT, "a" * 40, "stable", output)

    def test_copy_rejects_links_and_existing_destinations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = pathlib.Path(temporary_name)
            source = root / "source"
            source.write_text("payload", encoding="utf-8")
            link = root / "link"
            link.symlink_to(source.name)
            with self.assertRaisesRegex(self.bundle.ReleaseBundleError, "unsafe"):
                self.bundle.copy_exclusive(link, root / "copied")
            destination = root / "destination"
            destination.write_text("existing", encoding="utf-8")
            with self.assertRaisesRegex(self.bundle.ReleaseBundleError, "overwrite"):
                self.bundle.copy_exclusive(source, destination)

    def test_source_checksum_file_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = pathlib.Path(temporary_name)
            archive_name = "hostpanel-v3.4.1-hardened-r1-source.tar.gz"
            for name in (archive_name, *self.bundle.SOURCE_METADATA):
                (root / name).write_bytes(name.encode("utf-8"))
            self.bundle.write_source_checksums(root, archive_name)
            lines = (root / self.bundle.SOURCE_CHECKSUMS).read_text(
                encoding="utf-8"
            ).splitlines()
            self.assertEqual(
                [line.split()[1] for line in lines],
                [archive_name, *self.bundle.SOURCE_METADATA],
            )

    def test_source_compiles(self) -> None:
        compile(BUNDLE_PATH.read_text(encoding="utf-8"), str(BUNDLE_PATH), "exec")


if __name__ == "__main__":
    unittest.main()
