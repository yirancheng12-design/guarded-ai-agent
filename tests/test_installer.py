import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('project_installer', ROOT / 'tools/install.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='setup space ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def fake_create(self, folder):
        folder.mkdir()
        (folder / 'pyvenv.cfg').write_text('fixture')

    def test_new_install_and_repeat_preserve_existing_data(self):
        (self.root / 'user-data.txt').write_text('keep')
        with patch.object(installer.venv.EnvBuilder, 'create', side_effect=self.fake_create) as create, \
                patch.object(installer, 'checked') as run:
            installer.install(self.root)
            installer.install(self.root)
        self.assertEqual(create.call_count, 1)
        self.assertTrue(run.called)
        self.assertEqual((self.root / 'user-data.txt').read_text(), 'keep')
        self.assertTrue(json.loads((self.root / '.setup-state.json').read_text())['ok'])

    def test_failure_does_not_leave_success_marker(self):
        with patch.object(installer.venv.EnvBuilder, 'create', side_effect=self.fake_create), \
                patch.object(installer, 'checked', side_effect=subprocess.CalledProcessError(7, ['fixture'])):
            with self.assertRaises(subprocess.CalledProcessError):
                installer.install(self.root)
        self.assertFalse(json.loads((self.root / '.setup-state.json').read_text())['ok'])

    def test_existing_non_environment_is_not_overwritten(self):
        (self.root / '.venv').mkdir()
        (self.root / '.venv/keep.txt').write_text('keep')
        with self.assertRaisesRegex(ValueError, 'not a virtual environment'):
            installer.install(self.root)
        self.assertEqual((self.root / '.venv/keep.txt').read_text(), 'keep')

    def test_old_python_fails_before_writes(self):
        with patch.object(installer.sys, 'version_info', (3, 10)):
            with self.assertRaisesRegex(ValueError, '3.11'):
                installer.install(self.root)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_desktop_profile_rejected_off_windows(self):
        with patch.object(installer, 'PROJECT', 'guarded'), patch.object(installer.os, 'name', 'posix'):
            with self.assertRaisesRegex(ValueError, 'Windows'):
                installer.install(self.root, 'desktop')

    def test_desktop_installs_extras_before_browser_and_diagnostics(self):
        with patch.object(installer, 'PROJECT', 'guarded'):
            commands = installer.commands('desktop')
        self.assertEqual(commands[0][-1], '.[windows,assistant]')
        self.assertEqual(commands[1][-3:], ['playwright', 'install', 'chromium'])
        self.assertIn('doctor', commands[-2])

    def test_archive_install_has_no_package_or_model_download(self):
        for project in ['gpu', 'dlss']:
            with patch.object(installer, 'PROJECT', project):
                commands = installer.commands('core')
            self.assertFalse(any('pip' in c or 'ollama' in c for c in commands))

    def test_browser_override_is_preserved(self):
        (self.root / 'browsers').mkdir()
        with patch.object(installer, 'PROJECT', 'guarded'), \
                patch.object(installer.venv.EnvBuilder, 'create', side_effect=self.fake_create), \
                patch.object(installer, 'checked') as run, \
                patch.dict(os.environ, {'PLAYWRIGHT_BROWSERS_PATH': 'chosen-cache'}):
            installer.install(self.root)
        self.assertEqual(run.call_args.args[3]['PLAYWRIGHT_BROWSERS_PATH'], 'chosen-cache')

    def test_dry_run_from_other_directory_does_not_write(self):
        result = subprocess.run([sys.executable, str(ROOT / 'tools/install.py'), '--dry-run'],
                                cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)['commands'])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_relative_environment_executable_from_another_directory(self):
        installer.venv.EnvBuilder(with_pip=False, symlinks=os.name != 'nt').create(self.root / '.venv')
        with (self.root / 'probe.log').open('w', encoding='utf-8') as log:
            probe = ('import pathlib,sys; assert pathlib.Path(sys.prefix).resolve() == '
                     'pathlib.Path(".venv").resolve(); print("environment probe passed")')
            installer.checked([str(installer.environment_python()), '-c', probe],
                              self.root, log, os.environ.copy())
        self.assertIn('environment probe passed', (self.root / 'probe.log').read_text())

    def test_cmd_propagates_failure_from_custom_python(self):
        if os.name != 'nt':
            self.skipTest('Windows cmd wrapper')
        # Use the real wrapper with a deliberately invalid interpreter path.
        env = {**os.environ, 'SETUP_PYTHON': str(self.root / 'missing python.exe'), 'SETUP_NO_PAUSE': '1'}
        result = subprocess.run(['cmd', '/d', '/c', str(ROOT / 'Install.cmd')], cwd=self.root,
                                env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)

    def test_cmd_handles_space_checkout_from_another_directory(self):
        if os.name != 'nt':
            self.skipTest('Windows cmd wrapper')
        checkout = self.root / 'checkout space'
        (checkout / 'tools').mkdir(parents=True)
        (checkout / 'Install.cmd').write_bytes((ROOT / 'Install.cmd').read_bytes())
        (checkout / 'tools/install.py').write_text(
            'import json,pathlib,sys; pathlib.Path("wrapper.json").write_text(json.dumps(sys.argv[1:]))', encoding='utf-8')
        env = {**os.environ, 'SETUP_PYTHON': sys.executable, 'SETUP_NO_PAUSE': '1'}
        run = subprocess.run(['cmd', '/d', '/c', 'call', str(checkout / 'Install.cmd'), '--dry-run'],
                             cwd=self.root, env=env, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr + run.stdout)
        self.assertEqual(json.loads((checkout / 'wrapper.json').read_text()), ['--dry-run'])


if __name__ == '__main__':
    unittest.main()
