from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'


class InstalledWebTransactionLayoutTests(unittest.TestCase):
    def test_entry_imports_outer_transaction_from_installed_module_tree(self) -> None:
        modules = (
            'hostpanel_build_config.py',
            'hostpanel_build_packages.py',
            'hostpanel_build_operations.py',
            'hostpanel_build_operations_adapter.py',
            'hostpanel_build_optional_postcheck_adapter.py',
            'hostpanel_build_cli.py',
            'hostpanel_build_ssl.py',
            'hostpanel_build_extras.py',
            'hostpanel_build_extras_impl.py',
            'hostpanel_build_extras_state.py',
            'hostpanel_build_extras_state_impl.py',
            'hostpanel_build_entry.py',
            'hostpanel_build_powerdns_adapter.py',
            'hostpanel_build_powerdns_adapter_impl.py',
            'hostpanel_build_mongodb_adapter.py',
            'hostpanel_build_web_impl.py',
            'hostpanel_build_web_state.py',
            'hostpanel_build_web_transaction_adapter.py',
        )
        with tempfile.TemporaryDirectory() as directory:
            installed = pathlib.Path(directory)
            for name in modules:
                shutil.copy2(TOOLS / name, installed / name)

            probe = r'''
import pathlib
import sys
root = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(root))
import hostpanel_build_entry as entry
adapter = entry.web_transaction_adapter
if pathlib.Path(adapter.__file__).parent != root:
    raise SystemExit(f'outer adapter loaded outside installed tree: {adapter.__file__}')
if not callable(getattr(adapter, 'guarded_execute_build', None)):
    raise SystemExit('installed outer adapter has no guard')
postcheck = entry.optional_postcheck_adapter
if pathlib.Path(postcheck.__file__).parent != root:
    raise SystemExit(
        f'optional postcheck adapter loaded outside installed tree: {postcheck.__file__}'
    )
if not callable(getattr(postcheck, 'guarded_execute_build', None)):
    raise SystemExit('installed optional postcheck adapter has no guard')
state = root / 'hostpanel_build_web_state.py'
if not state.is_file():
    raise SystemExit('installed state helper is missing')
source = pathlib.Path(entry.__file__).read_text(encoding='utf-8')
for marker in (
    'web_transaction_adapter.guarded_execute_build(',
    'optional_postcheck_adapter.guarded_execute_build(',
):
    if marker not in source:
        raise SystemExit(f'installed entry is missing transaction layer: {marker}')
'''
            environment = dict(os.environ)
            environment.pop('PYTHONPATH', None)
            completed = subprocess.run(
                [sys.executable, '-c', probe, str(installed)],
                cwd=installed,
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_installer_module_manifest_matches_required_transaction_files(self) -> None:
        source = (TOOLS / 'install-hostpanel-build.sh').read_text(
            encoding='utf-8'
        )
        for name in (
            'hostpanel_build_optional_postcheck_adapter.py',
            'hostpanel_build_web_impl.py',
            'hostpanel_build_web_state.py',
            'hostpanel_build_web_transaction_adapter.py',
        ):
            self.assertEqual(source.count(f'  {name}\n'), 1)


if __name__ == '__main__':
    unittest.main()
