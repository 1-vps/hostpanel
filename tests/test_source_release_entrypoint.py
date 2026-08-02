from __future__ import annotations

import importlib.util
import pathlib
import sys
import tempfile
import types
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
ENTRYPOINT_PATH = ROOT / "tools" / "run-source-release-builder.py"


def load_entrypoint():
    spec = importlib.util.spec_from_file_location(
        "source_release_entrypoint_test", ENTRYPOINT_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load source release entrypoint")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class SourceReleaseEntrypointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.entrypoint = load_entrypoint()

    def test_overlay_phases_use_distinct_archive_paths(self) -> None:
        parent = pathlib.Path("/tmp/source-release")
        generated = self.entrypoint.overlay_archive_path(parent / "overlay")
        late = self.entrypoint.overlay_archive_path(parent / "late-overlay")
        self.assertEqual(generated, parent / "overlay.tar")
        self.assertEqual(late, parent / "late-overlay.tar")
        self.assertNotEqual(generated, late)
        with self.assertRaisesRegex(ValueError, "safe final component"):
            self.entrypoint.overlay_archive_path(pathlib.Path("/"))

    def test_phase_isolation_exports_to_destination_specific_tar(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            temporary = pathlib.Path(temporary_name)
            calls: list[tuple] = []

            def run_git(*arguments):
                calls.append(("run_git",) + arguments)
                return ""

            def run(arguments, *, stdout=None, **_kwargs):
                calls.append(("run", tuple(arguments), pathlib.Path(stdout.name)))
                return types.SimpleNamespace(stdout="")

            def extract(archive_path, destination, *, require_single_root):
                calls.append(
                    (
                        "extract",
                        pathlib.Path(archive_path),
                        pathlib.Path(destination),
                        require_single_root,
                    )
                )
                pathlib.Path(destination).mkdir()
                return ()

            builder = types.SimpleNamespace(
                run_git=run_git,
                run=run,
                extract_tar_safely=extract,
            )
            self.entrypoint.install_phase_isolation(builder)
            repository = temporary / "repository"
            repository.mkdir()
            generated = temporary / "overlay"
            late = temporary / "late-overlay"
            builder.export_overlay(repository, "a" * 40, ("app",), generated)
            builder.export_overlay(repository, "a" * 40, ("README.md",), late)

            run_archives = [call[2] for call in calls if call[0] == "run"]
            self.assertEqual(
                run_archives,
                [temporary / "overlay.tar", temporary / "late-overlay.tar"],
            )
            self.assertTrue((temporary / "overlay.tar").is_file())
            self.assertTrue((temporary / "late-overlay.tar").is_file())

    def test_source_compiles(self) -> None:
        compile(
            ENTRYPOINT_PATH.read_text(encoding="utf-8"),
            str(ENTRYPOINT_PATH),
            "exec",
        )


if __name__ == "__main__":
    unittest.main()
