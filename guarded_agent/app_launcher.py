"""Explicit app-open requests, installed executables only, verified windows."""
from dataclasses import asdict
from pathlib import Path
import json
import os
import re
import sqlite3
import subprocess
import time

APPS = {
    'wechat': {'name': '微信', 'aliases': ('微信', 'wechat', 'weixin'), 'executables': ('Weixin.exe', 'WeChat.exe')},
    'wegame': {'name': 'WeGame', 'aliases': ('wegame', '腾讯游戏平台'), 'executables': ('wegame.exe',)},
    'notepad': {'name': '记事本', 'aliases': ('记事本', 'notepad'), 'executables': ('notepad.exe',)},
    'calculator': {'name': '计算器', 'aliases': ('计算器', 'calculator', 'calc'), 'executables': ('calc.exe', 'CalculatorApp.exe')},
}


def open_request(text):
    """Full-command match: quoted explanations and compound commands never run."""
    match = re.fullmatch(r'\s*(?:请\s*)?(?:(?:帮我|替我|给我)\s*)?(?:打开|启动|运行|open\s+|launch\s+|start\s+)\s*([^\n]{1,60}?)\s*(?:一下|吧)?[。！.!]?\s*', text, re.I)
    if not match:
        return None
    name = match.group(1).strip().casefold()
    for identifier, app in APPS.items():
        if name in app['aliases']:
            return identifier
    if any(token in name for token in ('\n', '&', '|', ';', '并', '然后', '再', ':', '\\', '/')):
        return 'unsupported'
    matches = [key for key, app in installed_apps().items()
               if name in {str(alias).casefold() for alias in (app['name'], *app.get('aliases', ())) }]
    if len(matches) == 1:
        return matches[0]
    return 'unsupported'


def installed_apps():
    if os.name != 'nt':
        return {}
    import winreg
    candidates = {key: [] for key in APPS}
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            for key, app in APPS.items():
                for exe in app['executables']:
                    try:
                        with winreg.OpenKey(root, 'SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths\\' + exe, 0, winreg.KEY_READ | view) as item:
                            candidates[key].append(winreg.QueryValueEx(item, '')[0])
                    except OSError:
                        pass
            try:
                with winreg.OpenKey(root, 'SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall', 0, winreg.KEY_READ | view) as parent:
                    for index in range(winreg.QueryInfoKey(parent)[0]):
                        try:
                            with winreg.OpenKey(parent, winreg.EnumKey(parent, index)) as item:
                                name = winreg.QueryValueEx(item, 'DisplayName')[0].casefold()
                                for key in ('wechat', 'wegame'):
                                    if name not in APPS[key]['aliases']:
                                        continue
                                    try:
                                        icon = winreg.QueryValueEx(item, 'DisplayIcon')[0]
                                        candidates[key].append(re.sub(r',-?\d+\s*$', '', icon))
                                    except OSError:
                                        pass
                                    try:
                                        folder = winreg.QueryValueEx(item, 'InstallLocation')[0].strip('"')
                                        candidates[key].extend(str(Path(folder) / exe) for exe in APPS[key]['executables'])
                                    except OSError:
                                        pass
                        except OSError:
                            pass
            except OSError:
                pass
    system = Path(os.environ['SystemRoot']) / 'System32'
    candidates['notepad'].append(str(system / 'notepad.exe'))
    candidates['calculator'].append(str(system / 'calc.exe'))
    result = {}
    for key, paths in candidates.items():
        for raw in paths:
            path = Path(os.path.expandvars(raw.strip().strip('"')))
            if path.is_absolute() and path.is_file() and path.name.casefold() in {e.casefold() for e in APPS[key]['executables']}:
                result[key] = {**APPS[key], 'path': str(path.resolve())}
                break
    from .app_catalog import discover_executables
    for key, app in discover_executables().items():
        if not any(os.path.normcase(existing['path']) == os.path.normcase(app['path']) for existing in result.values()):
            result[key] = app
    return result


class AppBackend:
    """Dedicated Win32 worker; no UIA content, shell, credentials or messages."""
    def __init__(self):
        from .winapi import WinAPI
        self.api = WinAPI()
        self.api.initialize_dpi()
        self.sequence = 0
        self.lease = None

    def _matches(self, path, image):
        if os.path.normcase(path) == os.path.normcase(image):
            return True
        # Packaged Windows Notepad/Calculator may own the actual window.
        root = Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'WindowsApps'
        try:
            relative = Path(image).relative_to(root)
        except ValueError:
            return False
        package = relative.parts[0].casefold()
        name = Path(image).name.casefold()
        return ((Path(path).name.casefold() == 'notepad.exe' and package.startswith('microsoft.windowsnotepad_') and name == 'notepad.exe') or
                (Path(path).name.casefold() == 'calc.exe' and package.startswith('microsoft.windowscalculator_') and name == 'calculatorapp.exe'))

    def _process_image(self, pid):
        import ctypes as C
        from ctypes import wintypes as W
        query = self.api.k.QueryFullProcessImageNameW
        query.argtypes = [W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD)]
        query.restype = W.BOOL
        handle = self.api.k.OpenProcess(0x1000, False, pid)
        if not handle:
            return None
        try:
            buffer, size = C.create_unicode_buffer(32768), W.DWORD(32768)
            return buffer.value if query(handle, 0, buffer, C.byref(size)) else None
        finally:
            self.api.k.CloseHandle(handle)

    def _application_identity(self, path, identity):
        """Resolve a hosted UWP child, never trust a title or host PID alone.

        A UWP CoreWindow can move into ApplicationFrameHost after launch.
        Require the system host path and an independently identified visible
        child whose executable matches the installed application.
        """
        import ctypes as C
        from ctypes import wintypes as W
        image = self._process_image(identity.pid)
        if image and self._matches(path, image):
            return asdict(identity)
        host = str(Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32' / 'ApplicationFrameHost.exe')
        if not image or os.path.normcase(image) != os.path.normcase(host):
            return None
        matches = []
        callback_type = C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
        @callback_type
        def visit(hwnd, _):
            try:
                if self.api.u.IsWindowVisible(hwnd):
                    child = self.api.identity(hwnd)
                    child_image = self._process_image(child.pid)
                    if child_image and self._matches(path, child_image):
                        matches.append(asdict(child))
            except (OSError, ValueError, RuntimeError):
                pass
            return True
        self.api.u.EnumChildWindows.argtypes = [W.HWND, callback_type, W.LPARAM]
        self.api.u.EnumChildWindows.restype = W.BOOL
        self.api.u.EnumChildWindows(identity.hwnd, visit, 0)
        return matches[0] if len(matches) == 1 else None

    def sample(self, path, timeout=3):
        self.sequence += 1
        session = self.api.interactive_state()
        windows = []
        if session['interactive']:
            for identity, title in self.api.windows():
                if not title:
                    continue
                application = self._application_identity(path, identity)
                if not application:
                    continue
                try:
                    rect = self.api.rect(identity.hwnd)
                except (ValueError, OSError, RuntimeError):
                    # Zero-size helper HWNDs are not visible evidence.
                    continue
                windows.append({'identity': asdict(identity), 'application_identity': application,
                                'visible': not bool(self.api.u.IsIconic(identity.hwnd)) and rect.width > 0 and rect.height > 0,
                                'foreground': self.api.foreground() == identity.hwnd, 'area': rect.width * rect.height})
        windows.sort(key=lambda w: (w['foreground'], w['area']), reverse=True)
        return {'sequence': self.sequence, 'at': time.monotonic(), 'interactive': session['interactive'],
                'reason': session['reason'], 'window': windows[0] if windows else None}

    def open(self, identifier, path, timeout=3):
        # Recheck the installed catalog at the dispatch boundary.
        app = installed_apps().get(identifier)
        if not app or app['path'] != path:
            return {'execution_status': 'not_sent', 'reason': 'installed_application_changed'}
        state = self.sample(path)
        if not state['interactive']:
            return {'execution_status': 'not_sent', 'reason': state['reason']}
        if self.api.modifiers_down():
            return {'execution_status': 'not_sent', 'reason': 'user_key_or_mouse_button_held'}
        self.lease = self.api.acquire_input_lease(.5)
        if state['window']:
            identity = state['window']['identity']
            if asdict(self.api.identity(identity['hwnd'])) != identity:
                return {'execution_status': 'not_sent', 'reason': 'window_identity_changed'}
            try:
                self.api.activate(identity['hwnd'], timeout=1)
                return {'execution_status': 'sent', 'reason': 'existing_window_activation'}
            except RuntimeError as exc:
                return {'execution_status': 'uncertain', 'reason': str(exc)}
        try:
            if app.get('app_id'):
                os.startfile('shell:AppsFolder\\' + app['app_id'])
                return {'execution_status': 'sent', 'reason': 'registered_store_application_launched'}
            if app.get('shortcut') and app.get('arguments'):
                os.startfile(app['shortcut'], 'open')
                return {'execution_status': 'sent', 'reason': 'installed_shortcut_launched'}
            process = subprocess.Popen([path], cwd=str(Path(path).parent), shell=False,
                                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return {'execution_status': 'sent', 'reason': 'process_created', 'pid': process.pid}
        except OSError as exc:
            if getattr(exc, 'winerror', None) == 740:
                return {'execution_status': 'not_sent', 'reason': 'requires_windows_permission'}
            return {'execution_status': 'not_sent', 'reason': str(exc)}

    def open_with_shell(self, identifier, path, timeout=45):
        app = installed_apps().get(identifier)
        state = self.api.interactive_state()
        if not app or app['path'] != path or not state['interactive']:
            return {'execution_status': 'not_sent', 'reason': 'application_or_session_changed'}
        try:
            # Normal Windows launch; the human handles any UAC consent.
            os.startfile(path, 'open', cwd=str(Path(path).parent), show_cmd=1)
            return {'execution_status': 'sent', 'reason': 'windows_shell_launch'}
        except OSError as exc:
            return {'execution_status': 'not_sent', 'reason': 'system_prompt_cancelled' if getattr(exc, 'winerror', None) == 1223 else str(exc)}


class AppLauncher:
    def __init__(self, directory, backend_factory=None, discover=installed_apps, clock=time.monotonic, sleep=time.sleep):
        self.path = Path(directory) / 'app-actions.sqlite3'
        self.discover, self.clock, self.sleep = discover, clock, sleep
        if backend_factory is None:
            from .isolation import ProcessBackend
            backend_factory = lambda: ProcessBackend(factory=AppBackend)
        self.backend_factory = backend_factory

    def run(self, identifier, operation_id, cancelled=lambda: False, on_event=lambda _: None, verify_only=False):
        app = self.discover().get(identifier)
        if not app:
            return {'outcome': 'unsupported', 'execution_status': 'not_sent', 'text': '没有找到唯一的应用。请先 list_apps 选择实际应用标识；未收录的便携程序可用 register_app 登记本机 EXE。已运行的程序可直接用 list_windows 操作。'}
        path, samples, receipt = app['path'], [], {'execution_status': 'not_sent'}
        result = {'app': app['name'], 'operation_id': operation_id, 'samples': samples}
        if cancelled():
            return {**result, 'outcome': 'cancelled', **receipt, 'text': '已停止，尚未启动应用。'}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=5)
        db.execute('CREATE TABLE IF NOT EXISTS launches(path TEXT PRIMARY KEY,operation_id TEXT,status TEXT,result TEXT)')
        claimed, blocked = False, None
        try:
            with db:
                db.execute('BEGIN IMMEDIATE')
                blocked = db.execute("SELECT operation_id FROM launches WHERE path=? AND status='pending'", (path,)).fetchone()
                if not blocked and not verify_only:
                    db.execute('INSERT OR REPLACE INTO launches VALUES (?,?,?,NULL)', (path, operation_id, 'pending'))
                    claimed = True
            with self.backend_factory() as backend:
                first = backend.sample(path, timeout=3)
                if not first['interactive']:
                    result.update(outcome='unknown' if blocked else 'failed', text='桌面当前不可交互，未发送新的启动请求。请解锁电脑后再试。', reason=first['reason'], **receipt)
                else:
                    if not blocked and not verify_only:
                        if cancelled():
                            result.update(outcome='cancelled', text='已停止，尚未启动应用。', **receipt)
                        else:
                            on_event({'kind': 'opening_app', 'app': app['name']})
                            # Reservation is durable before crossing this boundary.
                            receipt = {'execution_status': 'uncertain'}
                            receipt = backend.open(identifier, path, timeout=45)
                            if receipt.get('reason') == 'requires_windows_permission' and receipt['execution_status'] == 'not_sent':
                                if cancelled():
                                    result.update(outcome='cancelled', execution_status='not_sent', text='已停止，尚未发出 Windows 启动请求。')
                                else:
                                    on_event({'kind': 'system_permission', 'app': app['name']})
                                    receipt = {'execution_status': 'uncertain'}
                                    receipt = backend.open_with_shell(identifier, path, timeout=45)
                            if receipt['execution_status'] == 'not_sent':
                                if not result.get('outcome'):
                                    if receipt.get('reason') == 'system_prompt_cancelled':
                                        result.update(outcome='cancelled', execution_status='not_sent', reason=receipt['reason'], text=f"Windows 启动请求已取消，未启动{app['name']}。")
                                    else:
                                        result.update(outcome='failed', execution_status='not_sent', reason=receipt.get('reason'),
                                                      text='Windows 没有启动应用。请核对系统权限确认或安装状态。' + receipt.get('reason', ''))
                    if not result.get('outcome'):
                        deadline = self.clock() + (2 if blocked or verify_only else 25)
                        previous, consecutive = None, 0
                        while self.clock() < deadline:
                            sample = backend.sample(path, timeout=3)
                            samples.append(sample)
                            window = sample.get('window')
                            fresh = not previous or sample['sequence'] > previous['sequence'] and sample['at'] > previous['at']
                            valid = fresh and sample['interactive'] and window and window['visible']
                            same = (previous and previous.get('window') and previous['window']['identity'] == window['identity']
                                    and previous['window'].get('application_identity') == window.get('application_identity')) if window else False
                            consecutive = (consecutive + 1 if same else 1) if valid else 0
                            previous = sample
                            if consecutive >= 3:
                                suffix = '' if window['foreground'] else ' 窗口已显示；Windows 暂未允许切到前台，可从任务栏进入。'
                                result.update(outcome='success', text=(f"已连续 3 次确认{app['name']}窗口可见；本次只读核验，未启动或激活。" if verify_only
                                              else f"已打开{app['name']}，已连续 3 次确认应用窗口可见。") + suffix,
                                              foreground=window['foreground'], **receipt)
                                break
                            if not sample['interactive']:
                                break
                            self.sleep(.2)
                        if not result.get('outcome'):
                            result.update(outcome='failed' if receipt['execution_status'] == 'not_sent' and not blocked else 'unknown',
                                text=('只读核验尚未发现连续可见的应用窗口；未发送启动或激活请求。' if verify_only else
                                      '还不能确认应用窗口已经显示。已停止，不会重复启动；上一次结果仍需核对。'),
                                reason='unresolved_app_launch' if blocked else receipt.get('reason', 'window_not_verified'), execution_status=receipt['execution_status'])
        except Exception as exc:
            result.update(outcome='unknown' if blocked or receipt['execution_status'] != 'not_sent' else 'failed',
                          text='启动结果未确认，已停止后续操作。', reason=str(exc), execution_status=receipt['execution_status'])
        finally:
            if claimed or result.get('outcome') == 'success':
                resolved = result.get('outcome') == 'success' or (receipt['execution_status'] == 'not_sent' and not blocked)
                with db:
                    db.execute('UPDATE launches SET status=?,result=? WHERE path=?', ('resolved' if resolved else 'pending', json.dumps(result, ensure_ascii=False), path))
            db.close()
        return result
