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


class OpenLiteSpeedInstalledLayoutTests(unittest.TestCase):
    def test_extensionless_installed_wrapper_loads_adjacent_implementation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            installed = pathlib.Path(directory)
            command = installed / 'hostpanel-build-web'
            shutil.copy2(TOOLS / 'hostpanel_build_web.py', command)
            command.chmod(0o755)
            for name in (
                'hostpanel_build_web_impl.py',
                'hostpanel_build_config.py',
            ):
                shutil.copy2(TOOLS / name, installed / name)

            probe = r'''
import importlib.machinery
import importlib.util
import pathlib
import sys
import types

root = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(root))
store = types.ModuleType('store')
store.connect = lambda: None
modules = types.ModuleType('modules')
modules.webserver = types.SimpleNamespace()
sys.modules['store'] = store
sys.modules['modules'] = modules
loader = importlib.machinery.SourceFileLoader(
    'installed_hostpanel_build_web', str(root / 'hostpanel-build-web')
)
spec = importlib.util.spec_from_loader(loader.name, loader)
if spec is None:
    raise SystemExit('could not create installed wrapper spec')
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
loader.exec_module(module)
expected = root / 'hostpanel_build_web_impl.py'
if module._IMPL_PATH != expected:
    raise SystemExit(f'unexpected implementation path: {module._IMPL_PATH}')
for name in (
    'main', 'prepare_openlitespeed', 'activate_openlitespeed',
    '_replace_atomic', '_restore_preparation',
):
    if not callable(getattr(module, name, None)):
        raise SystemExit(f'missing installed callable: {name}')
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

    def test_installer_compiles_and_copies_web_implementation(self) -> None:
        source = (TOOLS / 'install-hostpanel-build.sh').read_text(
            encoding='utf-8'
        )
        self.assertIn('hostpanel_build_web_impl.py', source)
        self.assertIn('"${MODULES[@]/#/$SOURCE_ROOT/tools/}"', source)
        self.assertIn(
            '"$SOURCE_ROOT/tools/hostpanel_build_web.py"', source
        )


if __name__ == '__main__':
    unittest.main()
