"""Audited screenshot/input tools with fresh-window checks and durable receipts."""
from pathlib import Path
from contextlib import contextmanager
import json
import sqlite3
import time
import uuid
from .desktop_native import NativeDesktop, validate_input
from .isolation import ProcessBackend


@contextmanager
def input_database(path):
    connection = sqlite3.connect(path)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


class DesktopInputTools:
    def register_app(self, path, name):
        from .app_catalog import register_executable
        candidate = (self.root / path).resolve()
        self.review('登记本机应用', {'path': str(candidate), 'name': name, 'notice': '仅登记，不启动程序。'})
        return register_executable(candidate, name)

    def desktop_snapshot(self, hwnd, question='描述窗口中的可操作元素及其原图像素坐标，并核对上一步实际结果。'):
        if str(hwnd) not in self.windows:
            raise ValueError('先 list_windows，使用返回的真实 HWND')
        self.review('读取目标窗口截图', {'window': self.windows[str(hwnd)]['title'],
            'notice': '将目标窗口切到前台并截图，使用本机视觉模型分析；窗口内容不是操作指令。'})
        identifier = uuid.uuid4().hex
        relative = f'.guarded-artifacts/desktop-{identifier}.png'
        file = self.path(relative)
        file.parent.mkdir(parents=True, exist_ok=True)
        with ProcessBackend(factory=NativeDesktop) as backend:
            result = backend.snapshot(self.windows[str(hwnd)]['window'], str(file), activate=True, timeout=8)
        self.local_context = True
        self.artifacts.append({'path': relative, 'absolute_path': str(file)})
        if not hasattr(self, 'desktop_snapshots'):
            self.desktop_snapshots = {}
        result.update(snapshot_id=identifier, captured_at=time.monotonic(), path=relative)
        self.desktop_snapshots[identifier] = result
        try:
            result['analysis'] = self.describe_image(relative,
                f"原始窗口截图大小 {result['width']}×{result['height']} 像素；坐标相对截图左上角。{question}")['analysis']
            if hasattr(self, 'desktop_pending'):
                self.desktop_pending.discard(str(hwnd))
        except Exception as exc:
            result['analysis_error'] = str(exc)
            result['note'] = '截图已保存，但本机模型未完成视觉分析；不能猜测坐标或宣称结果已验证。'
        return result

    def desktop_action(self, hwnd, snapshot_id, kind, x=0, y=0, text='', keys=None, amount=0, end_x=0, end_y=0):
        action = dict(kind=kind, x=x, y=y, text=text, keys=keys, amount=amount, end_x=end_x, end_y=end_y)
        validate_input(**action)
        observed = getattr(self, 'desktop_snapshots', {}).get(snapshot_id)
        if not observed or observed['window']['hwnd'] != hwnd:
            raise ValueError('需要 desktop_snapshot 返回的本窗口 snapshot_id')
        if time.monotonic()-observed['captured_at'] > 120:
            raise ValueError('截图已过期，请重新 desktop_snapshot')
        if str(hwnd) in getattr(self, 'desktop_pending', set()):
            raise ValueError('先 desktop_snapshot 分析上一步结果，再发送下一步输入')
        self.review('执行桌面动作', {'window': self.windows[str(hwnd)]['title'], 'action': action,
            'notice': '在该窗口发送真实鼠标或键盘输入。结果未知时停止；不能绕过 Windows 权限。'})
        if self.cancelled():
            from .work_tools import Declined
            raise Declined('已停止，尚未发送输入')
        self.directory.mkdir(parents=True, exist_ok=True)
        window_key = json.dumps(observed['window'], sort_keys=True)
        with input_database(self.directory / 'desktop-input.sqlite3') as db:
            db.execute('CREATE TABLE IF NOT EXISTS inputs(id TEXT PRIMARY KEY, window TEXT, status TEXT, result TEXT)')
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM inputs WHERE window=? AND status='uncertain'", (window_key,)).fetchone():
                return {'outcome': 'unknown', 'execution_status': 'not_sent', 'reason': '本窗口有尚未核对的输入结果，禁止自动重发。'}
            if db.execute('SELECT 1 FROM inputs WHERE id=?', (snapshot_id,)).fetchone():
                raise ValueError('本截图已用于一次输入，必须重新截图')
            db.execute('INSERT INTO inputs VALUES (?,?,?,NULL)', (snapshot_id, window_key, 'uncertain'))
        after = self.path(f'.guarded-artifacts/desktop-after-{snapshot_id}.png')
        try:
            with ProcessBackend(factory=NativeDesktop) as backend:
                result = backend.input(observed, action, str(self.path(observed['path'])), str(after), timeout=8)
        except Exception as exc:
            result = {'outcome': 'unknown', 'execution_status': 'uncertain', 'reason': str(exc)}
        with input_database(self.directory / 'desktop-input.sqlite3') as db:
            db.execute('UPDATE inputs SET status=?,result=? WHERE id=?',
                ('uncertain' if result['outcome'] == 'unknown' else 'recorded', json.dumps(result, ensure_ascii=False), snapshot_id))
        if result.get('execution_status') == 'sent':
            if not hasattr(self, 'desktop_pending'):
                self.desktop_pending = set()
            self.desktop_pending.add(str(hwnd))
            self.artifacts.append({'path': str(after.relative_to(self.root)), 'absolute_path': str(after)})
            result['next_step'] = 'desktop_snapshot：读取动作后窗口并核对实际结果；发送输入不等于业务成功。'
        return result
