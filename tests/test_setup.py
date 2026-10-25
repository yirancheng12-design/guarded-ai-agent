from contextlib import redirect_stdout
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from guarded_agent.cli import main
from guarded_agent.doctor import diagnose

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader('guarded_launcher', str(ROOT / 'launch.pyw'))
spec = importlib.util.spec_from_loader(loader.name, loader)
launcher = importlib.util.module_from_spec(spec)
loader.exec_module(launcher)


class SetupTests(unittest.TestCase):
    def test_offline_doctor_makes_no_network_call(self):
        with patch('urllib.request.OpenerDirector.open') as network:
            report = diagnose()
        network.assert_not_called()
        self.assertFalse(report['ollama_checked'])
        self.assertTrue(report['ok'])

    def test_optional_missing_packages_do_not_break_core_diagnostics(self):
        with patch('guarded_agent.doctor.importlib.util.find_spec', return_value=None):
            report = diagnose()
        self.assertTrue(report['ok'])
        self.assertFalse(next(check for check in report['checks'] if check['name'] == 'playwright')['ok'])

    def test_requested_ollama_failure_is_reported(self):
        with patch('guarded_agent.ollama_agent.OllamaClient.models', side_effect=OSError):
            report = diagnose(True)
        self.assertFalse(report['ok'])

    def test_declared_tools_support_is_checked_without_inference(self):
        with patch('guarded_agent.ollama_agent.OllamaClient.models', return_value=[{'name': 'fixture'}]), \
             patch('guarded_agent.ollama_agent.OllamaClient.request', return_value={'capabilities': ['completion']}) as request:
            report = diagnose(model='fixture')
        self.assertFalse(report['ok'])
        request.assert_called_once_with('/api/show', {'model': 'fixture'})

    def test_doctor_cli_returns_json_and_exit_code(self):
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(['doctor'])
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(output.getvalue())['ok'])

    def test_portable_paths_stay_inside_checkout(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            root = Path(directory) / 'checkout'
            configured = launcher.configure_environment(root)
            self.assertEqual(configured, root.resolve())
            for name in ('data', 'workspace', 'logs', 'cache/tmp'):
                self.assertTrue((root / name).is_dir())
            self.assertFalse((root.parent / 'data').exists())
            self.assertNotIn('PLAYWRIGHT_BROWSERS_PATH', os.environ)

    def test_existing_browser_path_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'PLAYWRIGHT_BROWSERS_PATH': 'custom'}, clear=True):
            launcher.configure_environment(directory)
            self.assertEqual(os.environ['PLAYWRIGHT_BROWSERS_PATH'], 'custom')

    def test_existing_portable_browser_folder_is_used(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            root = Path(directory)
            (root / 'browsers').mkdir()
            launcher.configure_environment(root)
            self.assertEqual(os.environ['PLAYWRIGHT_BROWSERS_PATH'], str(root / 'browsers'))
