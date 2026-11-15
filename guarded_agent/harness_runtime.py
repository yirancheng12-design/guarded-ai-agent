"""Guarded integration with an isolated, official DeepSeek Harness profile."""
from pathlib import Path
import http.cookiejar
import hashlib
import ctypes
import os
import json
import re
import subprocess
import sys
import threading
import time
import urllib.request

ROOT = Path(os.environ.get('GUARDED_INSTALL_ROOT') or Path(__file__).resolve().parents[1])
INSTALL = ROOT / 'harness'
LOG = ROOT / 'logs/harness.log'
ORIGIN = 'http://127.0.0.1:8775'
_lock = threading.Lock()


def managed_process_running():
    try:
        pid = int(json.loads((INSTALL / 'process.json').read_text(encoding='utf-8'))['pid'])
    except (OSError, ValueError, KeyError):
        return False
    if os.name != 'nt':
        return False
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
    finally:
        kernel.CloseHandle(handle)


def launch_url():
    """Read only the known loopback URL emitted by the managed runtime."""
    if not LOG.is_file():
        return None
    with LOG.open('rb') as stream:
        stream.seek(max(0, LOG.stat().st_size - 65536))
        text = stream.read().decode('utf-8', 'replace')
    matches = re.findall(r'http://127\.0\.0\.1:8775/\?token=[A-Za-z0-9_\-.~%]+', text)
    return matches[-1] if matches else None


def ready(url):
    if not url or not url.startswith(ORIGIN + '/?token='):
        return False
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    try:
        with opener.open(url, timeout=2) as response:
            return response.status == 200 and b'__DSH_BOOT__' in response.read(200000)
    except (OSError, ValueError):
        return False


def status():
    manifest = INSTALL / 'node_modules/@deepseek-ai/dsh/package.json'
    installed = manifest.is_file() and (INSTALL / 'runtime/node.exe').is_file()
    version = json.loads(manifest.read_text(encoding='utf-8')).get('version') if installed else None
    return {'installed': installed, 'running': ready(launch_url()) if installed else False,
        'version': version, 'origin': ORIGIN,
        'source': 'https://github.com/deepseek-ai/deepseek-harness',
        'data_directory': str(ROOT / 'data/harness')}


def install_workbench_background():
    """Install local background assets without restarting the workbench runtime."""
    source = Path(__file__).with_name('web_assets')
    animation = Path(__file__).with_name('native-renderer') / 'haloflux-background.html'
    variants = list((INSTALL / 'node_modules/.pnpm').glob(
        '@deepseek-ai+dsh-web-frontend@*/node_modules/@deepseek-ai/dsh-web-frontend/dist'))
    if not variants:
        return
    background_css = '''<style>
html,body,.app{height:100%!important;width:100%!important;overflow:hidden!important}
.app{display:block!important}
header,.panels,.ov{display:none!important}
.stage{position:fixed!important;inset:0!important;width:100%!important;height:100%!important}
</style><script>window.__guardedBackground=true;</script>'''
    html = animation.read_text(encoding='utf-8').replace('</head>', background_css + '</head>')
    marker = '<!-- guarded-workbench-background -->'
    version = hashlib.sha256((html + (source / 'workbench-background.js').read_text(encoding='utf-8') +
                              (source / 'workbench-background.css').read_text(encoding='utf-8')).encode()).hexdigest()[:12]
    injection = marker + f'\n<link rel="stylesheet" href="./assets/guarded-workbench-background.css?v={version}">' + \
        f'\n<script defer src="./assets/guarded-workbench-background.js?v={version}"></script>\n'
    def write_local(path, content):
        if path.is_file() and path.read_text(encoding='utf-8') == content:
            return
        temporary = path.with_name(path.name + '.guarded-tmp')
        temporary.write_text(content, encoding='utf-8')
        os.replace(temporary, path)  # Do not overwrite pnpm's shared hardlinked store.
    for dist in variants:
        assets = dist / 'assets'
        for extension in ('js', 'css'):
            write_local(assets / ('guarded-workbench-background.' + extension),
                        (source / ('workbench-background.' + extension)).read_text(encoding='utf-8'))
        write_local(assets / 'guarded-workbench-animation.html', html)
        index = dist / 'index.html'
        original = index.read_text(encoding='utf-8')
        if marker not in original:
            backup = ROOT / 'backups/workbench-background-embedded'
            backup.mkdir(parents=True, exist_ok=True)
            saved = backup / 'index.html'
            if not saved.exists():
                saved.write_text(original, encoding='utf-8')
            write_local(index, original.replace('</head>', injection + '</head>'))
        else:
            updated = re.sub(r'<!-- guarded-workbench-background -->\s*<link[^>]*guarded-workbench-background\.css[^>]*>\s*'
                             r'<script[^>]*guarded-workbench-background\.js[^>]*></script>\s*', injection, original)
            write_local(index, updated)


def open_workbench():
    """Start the installed profile if needed and return its authenticated URL."""
    if not (INSTALL / 'runtime/node.exe').is_file():
        raise ValueError('完整工作台尚未安装')
    install_workbench_background()
    # A live UI must remain accessible while the local model is loading.
    url = launch_url()
    if ready(url):
        warm_model()
        return {'url': url, 'origin': ORIGIN}
    with _lock:
        url = launch_url()
        if ready(url):
            warm_model()
            return {'url': url, 'origin': ORIGIN}
        python = ROOT / 'runtime/Scripts/python.exe'
        launcher = INSTALL / 'launch_harness.py'
        if not managed_process_running():
            result = subprocess.run([str(python), str(launcher)], cwd=str(INSTALL),
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), timeout=15)
            if result.returncode:
                raise RuntimeError('完整工作台启动失败，请检查 logs/harness.log')
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            url = launch_url()
            if ready(url):
                return {'url': url, 'origin': ORIGIN}
            time.sleep(.3)
        raise RuntimeError('完整工作台启动超时，请检查 logs/harness.log')


_model_lock = threading.Lock()
_model_last_started = float('-inf')


def warm_model():
    """Prepare the inference server without holding up a workbench connection."""
    global _model_last_started
    with _model_lock:
        if time.monotonic() - _model_last_started < 30:
            return
        if any(t.name == 'workbench-model-warmup' for t in threading.enumerate()):
            return
        _model_last_started = time.monotonic()
        def run():
            try:
                subprocess.run([str(ROOT / 'runtime/Scripts/python.exe'), str(INSTALL / 'ensure_qwen_iq3.py')],
                    cwd=str(INSTALL), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                    timeout=310, check=True)
            except (OSError, subprocess.SubprocessError) as error:
                print('Workbench model warmup deferred: ' + type(error).__name__, flush=True)
        threading.Thread(target=run, daemon=True, name='workbench-model-warmup').start()


_native_animation_lock = threading.Lock()


def native_animation_status():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open('http://127.0.0.1:8767/_status', timeout=.75) as response:
            result = json.loads(response.read(65_536).decode('utf-8'))
        age = result.get('canvas_age_seconds')
        result.update(running=True, ready=bool(result.get('canvas_ready') and
                      result.get('canvas_frames', 0) > 1 and isinstance(age, (int, float)) and age < 4))
        return result
    except (OSError, ValueError):
        return {'running': False, 'ready': False}


def start_native_animation(session_token, chat_port):
    with _native_animation_lock:
        return _start_native_animation(session_token, chat_port)


def _start_native_animation(session_token, chat_port):
    """Open the original workbench animation in a local native desktop window."""
    from importlib.util import find_spec

    script = Path(__file__).with_name('native_animation.py')
    if os.name != 'nt' or not script.is_file() or find_spec('webview') is None:
        raise RuntimeError('本机动画窗口组件未就绪')
    existing = native_animation_status()
    if existing['running'] and not existing['ready']:
        raise RuntimeError('动画窗口未完成绘制或已停止响应，请关闭动画窗口后重试')
    log_path = ROOT / 'logs/native-animation.log'
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open('a', encoding='utf-8') as log:
        process = subprocess.Popen(
            [sys.executable, str(script), '--standalone-preview'],
            cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            env={**os.environ, 'GUARDED_CHAT_TOKEN': session_token, 'GUARDED_CHAT_PORT': str(chat_port)},
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        state = native_animation_status()
        if state['ready']:
            return {'ok': True, 'pid': state.get('window_pid') or process.pid, **state}
        if state.get('canvas_error'):
            raise RuntimeError('动画绘制失败：' + state['canvas_error'])
        if process.poll() is not None and not state['running']:
            raise RuntimeError('动画窗口启动失败，请查看 logs/native-animation.log')
        time.sleep(.25)
    raise RuntimeError('动画画布未在限定时间内开始持续绘制，请重试')
