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


def fake_attestations(calls: list[tuple] | None = None):
    calls = calls if calls is not None else []

    def generate(source_root, **kwargs):
        calls.append(("generate", pathlib.Path(source_root), kwargs))

    def verify(archive_path, **kwargs):
        calls.append(("verify", pathlib.Path(archive_path), kwargs))

    def copy(source_root, output_dir):
        calls.append(("copy", pathlib.Path(source_root), pathlib.Path(output_dir)))

    return types.SimpleNamespace(
        generate_attestations=generate,
        verify_archive_attestations=verify,
        copy_attestations=copy,
    )


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
                write_deterministic_archive=lambda *_args: None,
            )
            self.entrypoint.install_phase_isolation(builder, fake_attestations())
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

    def test_python_bytecode_is_removed_before_archiving(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            source = pathlib.Path(temporary_name) / "source"
            cache = source / "tools" / "__pycache__"
            cache.mkdir(parents=True)
            (cache / "module.cpython-312.pyc").write_bytes(b"bytecode-a")
            (cache / "runtime.cpython-312.pyo").write_bytes(b"bytecode-b")
            orphan = source / "orphan.pyc"
            orphan.write_bytes(b"orphan")
            observed: list[list[str]] = []

            def write_archive(source_root, *_args):
                observed.append(
                    sorted(path.relative_to(source_root).as_posix() for path in source_root.rglob("*"))
                )

            builder = types.SimpleNamespace(
                run_git=lambda *_args: "",
                run=lambda *_args, **_kwargs: types.SimpleNamespace(stdout=""),
                extract_tar_safely=lambda *_args, **_kwargs: (),
                write_deterministic_archive=write_archive,
            )
            identity = types.SimpleNamespace(
                source_version="3.4.1",
                release_id="3.4.1-hardened-r1",
                archive_root="hostpanel-v3.4.1-hardened-r1",
            )
            self.entrypoint.install_phase_isolation(builder, fake_attestations())
            builder.write_deterministic_archive(
                source,
                pathlib.Path(temporary_name) / "out.tar.gz",
                identity,
                0,
            )

            self.assertEqual(len(observed), 1)
            self.assertNotIn("tools/__pycache__", observed[0])
            self.assertFalse(any(path.endswith((".pyc", ".pyo")) for path in observed[0]))
            self.assertFalse(cache.exists())
            self.assertFalse(orphan.exists())

    def test_attestations_wrap_the_archive_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = pathlib.Path(temporary_name)
            source = root / "source"
            source.mkdir()
            archive = root / "candidate.tar.gz"
            calls: list[tuple] = []

            def write_archive(*_args):
                calls.append(("archive",))

            builder = types.SimpleNamespace(
                run_git=lambda *_args: "",
                run=lambda *_args, **_kwargs: types.SimpleNamespace(stdout=""),
                extract_tar_safely=lambda *_args, **_kwargs: (),
                write_deterministic_archive=write_archive,
            )
            identity = types.SimpleNamespace(
                source_version="3.4.1",
                release_id="3.4.1-hardened-r1",
                archive_root="hostpanel-v3.4.1-hardened-r1",
            )
            self.entrypoint.install_phase_isolation(builder, fake_attestations(calls))
            builder.write_deterministic_archive(source, archive, identity, 123)
            self.assertEqual(
                [call[0] for call in calls],
                ["generate", "archive", "verify", "copy"],
            )

    def test_python_cache_cleanup_rejects_unexpected_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            source = pathlib.Path(temporary_name)
            cache = source / "__pycache__"
            cache.mkdir()
            (cache / "unexpected.txt").write_text("not bytecode", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "unexpected Python cache entry"):
                self.entrypoint.remove_python_bytecode(source)

    def test_source_compiles(self) -> None:
        compile(
            ENTRYPOINT_PATH.read_text(encoding="utf-8"),
            str(ENTRYPOINT_PATH),
            "exec",
        )


if __name__ == "__main__":
    unittest.main()
