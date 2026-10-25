"""Read-only setup diagnostics; network checks are explicitly opt-in."""
import importlib.util
import os
from pathlib import Path
import shutil
import sys


def diagnose(check_ollama=False, model=None):
    checks = []
    def add(name, ok, required, detail):
        checks.append({'name': name, 'ok': bool(ok), 'required': required, 'detail': detail})

    add('python', sys.version_info >= (3, 11), True, 'Python 3.11 or newer is required')
    assets = Path(__file__).with_name('web_assets')
    missing = [name for name in ('index.html', 'app.js', 'style.css') if not (assets / name).is_file()]
    add('web_assets', not missing, True, 'Missing: ' + ', '.join(missing) if missing else 'Bundled UI files are present')
    add('windows_desktop', os.name == 'nt', False, 'Native desktop control requires Windows; simulation does not')
    add('powershell', shutil.which('pwsh') or shutil.which('powershell'), False, 'Needed for run_command and repository test commands')
    add('git', shutil.which('git'), False, 'Needed for GitHub repository tools')
    for module, purpose in (('comtypes', 'Windows UI Automation'), ('PIL', 'screenshots'),
                            ('playwright', 'browser tools'), ('pypdf', 'PDF reading'),
                            ('docx', 'Word files'), ('openpyxl', 'Excel files'), ('pptx', 'PowerPoint files')):
        add(module, importlib.util.find_spec(module) is not None, False, purpose + ' (optional dependency)')
    models = []
    if check_ollama or model:
        from .ollama_agent import OllamaClient
        try:
            client = OllamaClient('', timeout=3)
            items = client.models()
            if not isinstance(items, list): raise ValueError('Invalid model list')
            models = [item['name'] for item in items if isinstance(item, dict) and isinstance(item.get('name'), str)]
            add('ollama', True, True, 'Local API responded; no inference was requested')
            add('installed_models', bool(models), True, 'Install a local model before using chat')
            if model:
                capabilities = client.request('/api/show', {'model': model}).get('capabilities', [])
                add('model_tools', 'tools' in capabilities, True, 'Selected model declares tools support; this does not certify task accuracy')
        except Exception as exc:
            add('ollama_check', False, True, 'Local Ollama check failed: ' + type(exc).__name__)
    return {'ok': all(check['ok'] for check in checks if check['required']), 'checks': checks,
            'ollama_checked': bool(check_ollama or model), 'models': models,
            'note': 'Optional checks describe available features. No packages, models, settings, or desktop actions were installed or changed.'}
