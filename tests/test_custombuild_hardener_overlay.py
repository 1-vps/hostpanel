from __future__ import annotations

import importlib.util
import pathlib
import re
import shutil
import stat
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
HARDENER_PATH = TOOLS / 'harden_install.py'
INSTALLER_PATH = TOOLS / 'install-hostpanel-build.sh'
BOOTSTRAP_PATH = ROOT / 'bootstrap-install.sh'


def load_hardener():
    name = '_hostpanel_custombuild_overlay_test'
    spec = importlib.util.spec_from_file_location(name, HARDENER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError('could not load HostPanel hardener entrypoint')
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


HARDENER = load_hardener()


def shell_array(source: str, name: str) -> tuple[str, ...]:
    match = re.search(
        rf'^{re.escape(name)}=\(\n(?P<body>.*?)^\)$',
        source,
        flags=re.MULTILINE | re.DOTALL,
    )
    if match is None:
        raise AssertionError(f'could not find shell array {name}')
    values = []
    for raw in match.group('body').splitlines():
        value = raw.strip()
        if value and not value.startswith('#'):
            values.append(value.strip('"\''))
    return tuple(values)


class CustomBuildHardenerOverlayTests(unittest.TestCase):
    def test_blob_manifest_matches_every_current_runtime_file(self) -> None:
        self.assertEqual(
            set(HARDENER.CUSTOMBUILD_RUNTIME_MODES),
            set(HARDENER.CUSTOMBUILD_RUNTIME_BLOBS),
        )
        for relative, expected in HARDENER.CUSTOMBUILD_RUNTIME_BLOBS.items():
            path = ROOT.joinpath(*pathlib.PurePosixPath(relative).parts)
            self.assertTrue(path.is_file(), relative)
            self.assertFalse(path.is_symlink(), relative)
            actual = HARDENER._git_blob_digest(path.read_bytes())
            self.assertEqual(actual, expected, relative)

    def test_hardener_manifest_exactly_matches_installer_inputs(self) -> None:
        source = INSTALLER_PATH.read_text(encoding='utf-8')
        source_match = re.search(
            r'^SOURCE="\$SOURCE_ROOT/tools/(?P<name>[^"]+)"$',
            source,
            flags=re.MULTILINE,
        )
        self.assertIsNotNone(source_match)
        runtime = {
            f"tools/{source_match.group('name')}",
            'tools/install-hostpanel-build.sh',
        }
        runtime.update(
            f'tools/{name}' for name in shell_array(source, 'MODULES')
        )
        runtime.update(
            f'tools/{name}' for name in shell_array(source, 'EXECUTABLES')
        )
        self.assertEqual(runtime, set(HARDENER.CUSTOMBUILD_RUNTIME_MODES))

    def test_modes_are_fixed_by_installed_role_not_source_inode(self) -> None:
        for relative in HARDENER.CUSTOMBUILD_EXECUTABLES:
            self.assertEqual(HARDENER.CUSTOMBUILD_RUNTIME_MODES[relative], 0o755)
        for relative in HARDENER.CUSTOMBUILD_MODULES:
            self.assertEqual(HARDENER.CUSTOMBUILD_RUNTIME_MODES[relative], 0o644)
        self.assertFalse(
            set(HARDENER.CUSTOMBUILD_EXECUTABLES)
            & set(HARDENER.CUSTOMBUILD_MODULES)
        )

    def test_overlay_copies_exact_bytes_and_survives_second_no_git_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory) / 'source'
            target.mkdir(mode=0o755)
            HARDENER.synchronize_custombuild_runtime(ROOT, target)

            for relative, mode in HARDENER.CUSTOMBUILD_RUNTIME_MODES.items():
                source = ROOT.joinpath(*pathlib.PurePosixPath(relative).parts)
                destination = target.joinpath(
                    *pathlib.PurePosixPath(relative).parts
                )
                self.assertEqual(destination.read_bytes(), source.read_bytes())
                self.assertEqual(stat.S_IMODE(destination.stat().st_mode), mode)

            # The generated source tree has no .git directory. It must still
            # verify cleanly during install.sh's second hardener invocation.
            HARDENER.synchronize_custombuild_runtime(target, target)

    def test_second_pass_rejects_a_tampered_runtime_file_without_git(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory) / 'source'
            target.mkdir(mode=0o755)
            HARDENER.synchronize_custombuild_runtime(ROOT, target)
            victim = target / 'tools/hostpanel_build_entry.py'
            victim.write_text(
                victim.read_text(encoding='utf-8') + '\n# tampered\n',
                encoding='utf-8',
            )
            with self.assertRaisesRegex(
                SystemExit, 'does not match its Git blob'
            ):
                HARDENER.synchronize_custombuild_runtime(target, target)

    def test_overlay_rejects_a_symlink_destination(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory) / 'source'
            tools = target / 'tools'
            tools.mkdir(parents=True, mode=0o755)
            victim = tools / 'hostpanel_build_config.py'
            victim.symlink_to('/etc/passwd')
            with self.assertRaisesRegex(
                SystemExit, 'unsafe CustomBuild overlay target'
            ):
                HARDENER.synchronize_custombuild_runtime(ROOT, target)

    def test_overlay_runs_before_the_blob_pinned_driver(self) -> None:
        source = HARDENER_PATH.read_text(encoding='utf-8')
        main = source.split('def main() -> None:', 1)[1]
        synchronize = main.index('synchronize_custombuild_runtime(')
        driver = main.index('DRIVER.main()')
        self.assertLess(synchronize, driver)

    def test_bootstrap_passes_the_commit_verified_checkout_to_hardener(self) -> None:
        source = BOOTSTRAP_PATH.read_text(encoding='utf-8')
        marker = 'HP_HARDENER_SOURCE_ROOT="$CHECKOUT"'
        invocation = 'python3 "$SOURCE_ROOT/tools/harden_install.py"'
        self.assertIn(marker, source)
        self.assertIn(invocation, source)
        self.assertLess(source.index(marker), source.index(invocation))


if __name__ == '__main__':
    unittest.main()
