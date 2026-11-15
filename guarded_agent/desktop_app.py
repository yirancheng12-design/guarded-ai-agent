"""Windows desktop entry point for the existing local Guarded service."""
from pathlib import Path
import ctypes
from ctypes import wintypes
import hashlib
import json
import subprocess
import sys
import time
import urllib.request

URL = 'http://127.0.0.1:8765'
TITLE = 'Guarded'


def service_ready():
    # Ignore system proxies for the local service.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(URL + '/health', timeout=1) as response:
            return json.load(response).get('app') == 'guarded-chat'
    except (OSError, ValueError):
        return False


def ensure_service(root):
    if service_ready():
        return
    process = subprocess.Popen(
        [sys.executable, str(root / 'launch.pyw'), '--server-only'],
        cwd=root, creationflags=subprocess.CREATE_NO_WINDOW,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if service_ready():
            return
        if process.poll() is not None:
            raise RuntimeError('本地服务启动失败，请查看 logs/chat.log')
        time.sleep(.2)
    raise RuntimeError('等待本地服务超时，请查看 logs/chat.log')


def run(root):
    import webview

    root = Path(root)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    identity = hashlib.sha256(str(root.resolve()).lower().encode()).hexdigest()[:20]
    mutex = kernel.CreateMutexW(None, False, 'Local\\GuardedDesktop-' + identity)
    already_open = ctypes.get_last_error() == 183
    if not mutex:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if already_open:
            user = ctypes.WinDLL('user32', use_last_error=True)
            user.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
            user.FindWindowW.restype = wintypes.HWND
            user.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
            user.SetForegroundWindow.argtypes = [wintypes.HWND]
            for _ in range(100):
                hwnd = user.FindWindowW(None, TITLE)
                if hwnd:
                    user.ShowWindow(hwnd, 9)
                    user.SetForegroundWindow(hwnd)
                    return
                time.sleep(.1)
            return
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('Guarded.Desktop')
        ensure_service(root)
        profile = root / 'data/desktop-webview'
        profile.mkdir(parents=True, exist_ok=True)
        webview.create_window(
            TITLE, URL, width=1440, height=940, min_size=(900, 640),
            background_color='#0b1424', text_select=True,
        )
        webview.start(
            gui='edgechromium', private_mode=False, storage_path=str(profile),
            icon=str(Path(__file__).with_name('web_assets') / 'boot-suite/lib/assets/guarded-icon.ico'),
        )
        # The service remains alive so closing the window never interrupts a task.
    finally:
        kernel.CloseHandle(mutex)
