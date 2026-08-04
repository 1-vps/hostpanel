from __future__ import annotations

import importlib.util
import pathlib
import stat
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
HARDENER_PATH = ROOT / 'tools/harden_install.py'


def load_hardener():
    name = '_hostpanel_hardener_support_test'
    spec = importlib.util.spec_from_file_location(name, HARDENER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError('could not load hardener wrapper')
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


HARDENER = load_hardener()


class HardenerSecondPassSupportTests(unittest.TestCase):
    def test_support_manifest_matches_preserved_files(self) -> None:
        expected = {
            'tools/harden_install_entry_impl.py': (
                HARDENER.EXPECTED_IMPLEMENTATION_BLOB, 0o644
            ),
            'tools/harden_install_driver.py': (
                HARDENER.EXPECTED_DRIVER_BLOB, 0o644
            ),
        }
        self.assertEqual(HARDENER.HARDENER_SUPPORT_FILES, expected)
        for relative, (blob, _mode) in expected.items():
            path = ROOT.joinpath(*pathlib.PurePosixPath(relative).parts)
            self.assertEqual(
                HARDENER._git_blob_digest(path.read_bytes()), blob, relative
            )

    def test_import_exposes_the_current_update_installer_pin(self) -> None:
        self.assertEqual(
            HARDENER.EXPECTED_UPDATE_AGENT_BLOBS[
                'tools/install-update-agent.sh'
            ],
            HARDENER.EXPECTED_UPDATE_INSTALLER_BLOB,
        )

    def test_first_pass_installs_support_for_no_git_second_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory) / 'source'
            target.mkdir(mode=0o755)
            HARDENER.synchronize_custombuild_runtime(ROOT, target)

            for relative, (_blob, mode) in HARDENER.HARDENER_SUPPORT_FILES.items():
                source = ROOT.joinpath(*pathlib.PurePosixPath(relative).parts)
                installed = target.joinpath(
                    *pathlib.PurePosixPath(relative).parts
                )
                self.assertEqual(installed.read_bytes(), source.read_bytes())
                self.assertEqual(stat.S_IMODE(installed.stat().st_mode), mode)

            self.assertFalse((target / '.git').exists())
            HARDENER.synchronize_custombuild_runtime(target, target)

    def test_second_pass_rejects_missing_driver(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory) / 'source'
            target.mkdir(mode=0o755)
            HARDENER.synchronize_custombuild_runtime(ROOT, target)
            (target / 'tools/harden_install_driver.py').unlink()
            with self.assertRaisesRegex(
                SystemExit, 'missing reviewed runtime file'
            ):
                HARDENER.synchronize_custombuild_runtime(target, target)

    def test_second_pass_rejects_tampered_implementation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory) / 'source'
            target.mkdir(mode=0o755)
            HARDENER.synchronize_custombuild_runtime(ROOT, target)
            implementation = target / 'tools/harden_install_entry_impl.py'
            implementation.write_bytes(implementation.read_bytes() + b'\n# tampered\n')
            with self.assertRaisesRegex(
                SystemExit, 'does not match its Git blob'
            ):
                HARDENER.synchronize_custombuild_runtime(target, target)


if __name__ == '__main__':
    unittest.main()
