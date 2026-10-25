"""Window-bound physical input for applications without UI Automation."""
from dataclasses import asdict
import time
from .winapi import WinAPI, INPUT, MOUSEINPUT, KEYBDINPUT
from .model import WindowIdentity
from .geometry import sendinput_absolute

KEYS = {'CTRL': 0x11, 'ALT': 0x12, 'SHIFT': 0x10, 'WIN': 0x5B,
        'ENTER': 13, 'TAB': 9, 'ESC': 27, 'SPACE': 32, 'BACKSPACE': 8,
        'DELETE': 46, 'HOME': 36, 'END': 35, 'PAGEUP': 33, 'PAGEDOWN': 34,
        'LEFT': 37, 'UP': 38, 'RIGHT': 39, 'DOWN': 40,
        **{chr(n): n for n in range(65, 91)}, **{str(n): 48+n for n in range(10)},
        **{f'F{n}': 111+n for n in range(1, 25)}}


def validate_input(kind, x=0, y=0, text='', keys=None, amount=0, end_x=0, end_y=0):
    if kind not in {'click', 'double_click', 'right_click', 'move', 'drag', 'scroll', 'press', 'type_text'}:
        raise ValueError('不支持的输入类型')
    if any(type(v) is not int or not 0 <= v <= 100000 for v in (x, y, end_x, end_y)):
        raise ValueError('坐标必须是截图内的非负整数像素')
    if not isinstance(text, str) or len(text) > 4000 or (kind == 'type_text' and not text):
        raise ValueError('输入文本长度需要在 1 到 4000 字之间')
    if keys is not None and (not isinstance(keys, list) or len(keys) > 6 or
            any(not isinstance(k, str) or k.upper() not in KEYS for k in keys)):
        raise ValueError('按键使用 CTRL/ALT/SHIFT/WIN、字母数字、F1-F24、ENTER/TAB/ESC 或方向键')
    if kind == 'press' and (not keys or len(set(k.upper() for k in keys)) != len(keys)):
        raise ValueError('需要不重复的按键组合')
    if type(amount) is not int or abs(amount) > 10 or (kind == 'scroll' and amount == 0):
        raise ValueError('滚轮量为 -10 到 10 的非零整数')


class NativeDesktop:
    def __init__(self):
        self.api = WinAPI()
        self.api.initialize_dpi()
        self.lease = None

    def activate(self, hwnd):
        try:
            return self.api.activate(hwnd, timeout=.5)
        except RuntimeError as exc:
            if str(exc) != 'foreground_activation_denied':
                raise
        # Share input queues briefly on the same unlocked desktop. No keys are
        # injected to steal focus; Windows access checks still apply.
        import ctypes as C
        from ctypes import wintypes as W
        attach = self.api.u.AttachThreadInput
        attach.argtypes, attach.restype = [W.DWORD, W.DWORD, W.BOOL], W.BOOL
        self.api.k.GetCurrentThreadId.restype = W.DWORD
        current = self.api.k.GetCurrentThreadId()
        threads = {self.api.u.GetWindowThreadProcessId(hwnd, None),
                   self.api.u.GetWindowThreadProcessId(self.api.foreground(), None)} - {0, current}
        attached = []
        try:
            if not self.api.interactive_state()['interactive'] or self.api.modifiers_down():
                raise RuntimeError('desktop_or_input_state_changed')
            for thread in threads:
                if attach(current, thread, True):
                    attached.append(thread)
            self.api.u.BringWindowToTop(hwnd)
            self.api.u.SetForegroundWindow(hwnd)
        finally:
            for thread in reversed(attached):
                attach(current, thread, False)
        if self.api.foreground() != hwnd:
            raise RuntimeError('foreground_activation_denied;请手动切到目标窗口后重新截图')

    def context(self, window):
        state = self.api.interactive_state()
        if not state['interactive']:
            raise RuntimeError(state['reason'])
        if asdict(self.api.identity(window['hwnd'])) != window:
            raise RuntimeError('window_identity_changed')
        if self.api.foreground() != window['hwnd']:
            raise RuntimeError('foreground_lost')
        return self.api.rect(window['hwnd'])

    def list_windows(self, timeout=5):
        return [{'window': asdict(window), 'title': title} for window, title in self.api.windows() if title]

    def snapshot(self, window, path, activate=False, timeout=5):
        from PIL import ImageGrab
        if activate:
            state = self.api.interactive_state()
            if not state['interactive']:
                raise RuntimeError(state['reason'])
            if self.api.modifiers_down():
                raise RuntimeError('user_key_or_mouse_button_held')
            if self.api.identity(window['hwnd']) != WindowIdentity(**window):
                raise RuntimeError('window_identity_changed')
            self.lease = self.api.acquire_input_lease(.5)
            self.activate(window['hwnd'])
        rect = self.context(window)
        picture = ImageGrab.grab(bbox=rect.bbox, all_screens=True).convert('RGB')
        if self.context(window) != rect or picture.size != (rect.width, rect.height):
            raise RuntimeError('window_changed_during_capture')
        picture.save(path)
        return {'window': window, 'physical_rect': asdict(rect), 'screenshot': path,
                'width': rect.width, 'height': rect.height, 'outcome': 'success'}

    def input(self, observation, action, before_path, after_path, timeout=8):
        from PIL import Image, ImageGrab, ImageChops, ImageDraw
        from ctypes.wintypes import POINT
        from .vision import cursor_mask
        validate_input(**action)
        window = observation['window']
        rect = self.context(window)
        if asdict(rect) != observation['physical_rect'] or self.api.modifiers_down():
            return {'outcome': 'failed', 'execution_status': 'not_sent', 'reason': 'geometry_changed_or_user_input_held'}
        self.lease = self.api.acquire_input_lease(.5)
        current = ImageGrab.grab(bbox=rect.bbox, all_screens=True).convert('RGB')
        with Image.open(before_path) as expected:
            if expected.size != current.size:
                return {'outcome': 'failed', 'execution_status': 'not_sent', 'reason': 'screenshot_size_changed'}
            channels = ImageChops.difference(expected.convert('RGB'), current).split()
            difference = ImageChops.lighter(ImageChops.lighter(channels[0], channels[1]), channels[2])
            mask = cursor_mask(*self.api.cursor(), rect)
            ImageDraw.Draw(difference).rectangle((mask.left, mask.top, mask.left+mask.width-1, mask.top+mask.height-1), fill=0)
            delta = sum(difference.histogram()[25:]) / (rect.width*rect.height)
        if delta > .01:
            return {'outcome': 'failed', 'execution_status': 'not_sent', 'reason': 'screenshot_changed_read_again'}
        kind = action['kind']
        x, y = action.get('x', 0), action.get('y', 0)
        def point(px, py):
            if not (0 <= px < rect.width and 0 <= py < rect.height):
                raise ValueError('坐标超出截图')
            hit = self.api.u.WindowFromPoint(POINT(rect.left+px, rect.top+py))
            if int(self.api.u.GetAncestor(hit, 2) or 0) != window['hwnd']:
                raise ValueError('目标点被其他窗口遮挡')
            dx, dy = sendinput_absolute(rect.left+px, rect.top+py, self.api.desktop())
            return INPUT(type=0, mi=MOUSEINPUT(dx=dx, dy=dy, dwFlags=0xC001))
        events = []
        if kind in {'click', 'double_click', 'right_click', 'move', 'drag', 'scroll'}:
            events.append(point(x, y))
            if kind == 'scroll':
                events.append(INPUT(type=0, mi=MOUSEINPUT(mouseData=(action['amount']*120) & 0xFFFFFFFF, dwFlags=0x0800)))
            elif kind == 'drag':
                events.append(INPUT(type=0, mi=MOUSEINPUT(dwFlags=2)))
                events.append(point(action['end_x'], action['end_y']))
                events.append(INPUT(type=0, mi=MOUSEINPUT(dwFlags=4)))
            elif kind != 'move':
                down, up = (8, 16) if kind == 'right_click' else (2, 4)
                for _ in range(2 if kind == 'double_click' else 1):
                    events += [INPUT(type=0, mi=MOUSEINPUT(dwFlags=down)), INPUT(type=0, mi=MOUSEINPUT(dwFlags=up))]
        elif kind == 'type_text':
            events = self.api.text_events(action['text'])
        else:
            codes = [KEYS[k.upper()] for k in action['keys']]
            events = [INPUT(type=1, ki=KEYBDINPUT(wVk=k)) for k in codes]
            events += [INPUT(type=1, ki=KEYBDINPUT(wVk=k, dwFlags=2)) for k in reversed(codes)]
        if self.context(window) != rect or self.api.modifiers_down():
            return {'outcome': 'failed', 'execution_status': 'not_sent', 'reason': 'context_changed_before_input'}
        accepted, requested, _ = self.api.send(events)
        if accepted != requested:
            return {'outcome': 'unknown' if accepted else 'failed', 'execution_status': 'uncertain' if accepted else 'not_sent',
                    'reason': 'partial_or_blocked_input', 'accepted': accepted, 'requested': requested}
        time.sleep(.25)
        after = self.snapshot(window, after_path)
        return {'outcome': 'success', 'execution_status': 'sent', 'verified': False, 'after': after,
                'reason': '仅确认输入已发送并保存动作后截图；必须读取截图核对业务结果，不能据此宣称任务完成。'}
