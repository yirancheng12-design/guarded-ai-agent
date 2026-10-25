"""Typed Win32 boundary. Importing this module never activates a window."""
import ctypes as C
from ctypes import wintypes as W
import os
import time
from .model import Rect, WindowIdentity
from .geometry import sendinput_absolute


class MOUSEINPUT(C.Structure):
    _fields_ = [("dx", W.LONG), ("dy", W.LONG), ("mouseData", W.DWORD),
                ("dwFlags", W.DWORD), ("time", W.DWORD), ("dwExtraInfo", C.c_size_t)]


class KEYBDINPUT(C.Structure):
    _fields_ = [("wVk", W.WORD), ("wScan", W.WORD), ("dwFlags", W.DWORD),
                ("time", W.DWORD), ("dwExtraInfo", C.c_size_t)]


class HARDWAREINPUT(C.Structure):
    _fields_ = [("uMsg", W.DWORD), ("wParamL", W.WORD), ("wParamH", W.WORD)]


class INPUTUNION(C.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(C.Structure):
    _anonymous_ = ("data",)
    _fields_ = [("type", W.DWORD), ("data", INPUTUNION)]


class WinAPI:
    def __init__(self):
        if os.name != "nt":
            raise OSError("Windows backend requires Windows")
        self.u = C.WinDLL("user32", use_last_error=True)
        self.k = C.WinDLL("kernel32", use_last_error=True)
        prototypes = {
            "IsWindow": ([W.HWND], W.BOOL),
            "IsWindowVisible": ([W.HWND], W.BOOL),
            "IsIconic": ([W.HWND], W.BOOL),
            "ShowWindowAsync": ([W.HWND, C.c_int], W.BOOL),
            "BringWindowToTop": ([W.HWND], W.BOOL),
            "GetWindowThreadProcessId": ([W.HWND, C.POINTER(W.DWORD)], W.DWORD),
            "GetForegroundWindow": ([], W.HWND),
            "SetForegroundWindow": ([W.HWND], W.BOOL),
            "GetWindowRect": ([W.HWND, C.POINTER(W.RECT)], W.BOOL),
            "GetDpiForWindow": ([W.HWND], W.UINT),
            "GetAncestor": ([W.HWND, W.UINT], W.HWND),
            "GetWindow": ([W.HWND, W.UINT], W.HWND),
            "WindowFromPoint": ([W.POINT], W.HWND),
            "GetSystemMetrics": ([C.c_int], C.c_int),
            "GetPhysicalCursorPos": ([C.POINTER(W.POINT)], W.BOOL),
            "GetAsyncKeyState": ([C.c_int], C.c_short),
            "GetThreadDpiAwarenessContext": ([], W.HANDLE),
            "AreDpiAwarenessContextsEqual": ([W.HANDLE, W.HANDLE], W.BOOL),
            "SetProcessDpiAwarenessContext": ([W.HANDLE], W.BOOL),
            "SendInput": ([W.UINT, C.POINTER(INPUT), C.c_int], W.UINT),
            "GetWindowTextLengthW": ([W.HWND], C.c_int),
            "GetWindowTextW": ([W.HWND, W.LPWSTR, C.c_int], C.c_int),
        }
        for name, (args, result) in prototypes.items():
            f = getattr(self.u, name)
            f.argtypes, f.restype = args, result
        for name, args, result in [
            ("OpenProcess", [W.DWORD, W.BOOL, W.DWORD], W.HANDLE),
            ("CloseHandle", [W.HANDLE], W.BOOL),
            ("GetProcessTimes", [W.HANDLE] + [C.POINTER(W.FILETIME)] * 4, W.BOOL),
            ("CreateMutexW", [C.c_void_p, W.BOOL, W.LPCWSTR], W.HANDLE),
            ("WaitForSingleObject", [W.HANDLE, W.DWORD], W.DWORD),
            ("ReleaseMutex", [W.HANDLE], W.BOOL),
        ]:
            f = getattr(self.k, name)
            f.argtypes, f.restype = args, result

    def acquire_input_lease(self, timeout):
        handle = self.k.CreateMutexW(None, False, "Local\\GuardedAgentDesktop-v1")
        if not handle:
            raise C.WinError(C.get_last_error())
        result = self.k.WaitForSingleObject(handle, max(0, int(timeout * 1000)))
        if result not in (0, 0x80):  # Acquired, or previous worker exited.
            self.k.CloseHandle(handle)
            raise RuntimeError("another_guarded_agent_owns_input_lease")
        return handle

    def initialize_dpi(self):
        desired = C.c_void_p(-4)  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        if not self.u.AreDpiAwarenessContextsEqual(self.u.GetThreadDpiAwarenessContext(), desired):
            self.u.SetProcessDpiAwarenessContext(desired)
        if not self.u.AreDpiAwarenessContextsEqual(self.u.GetThreadDpiAwarenessContext(), desired):
            raise RuntimeError("PerMonitorV2 unavailable; launch a fresh worker before creating HWNDs")

    def identity(self, hwnd):
        if not self.u.IsWindow(hwnd):
            raise RuntimeError("window_no_longer_exists")
        pid = W.DWORD()
        if not self.u.GetWindowThreadProcessId(hwnd, C.byref(pid)):
            raise C.WinError(C.get_last_error())
        handle = self.k.OpenProcess(0x1000, False, pid.value)
        if not handle:
            raise C.WinError(C.get_last_error())
        try:
            times = [W.FILETIME() for _ in range(4)]
            if not self.k.GetProcessTimes(handle, *(C.byref(t) for t in times)):
                raise C.WinError(C.get_last_error())
            created = times[0].dwLowDateTime | (times[0].dwHighDateTime << 32)
            return WindowIdentity(int(hwnd), pid.value, created)
        finally:
            self.k.CloseHandle(handle)

    def rect(self, hwnd):
        r = W.RECT()
        if not self.u.GetWindowRect(hwnd, C.byref(r)):
            raise C.WinError(C.get_last_error())
        return Rect(r.left, r.top, r.right - r.left, r.bottom - r.top)

    def desktop(self):
        return Rect(*(self.u.GetSystemMetrics(i) for i in (76, 77, 78, 79)))

    def foreground(self):
        hwnd = self.u.GetForegroundWindow()
        # Some UIA Win32 proxies put a child control in the foreground (Invoke
        # can do this). Bind the containing top-level window, not the raw child.
        # GA_ROOT follows parents only; GA_ROOTOWNER would incorrectly accept
        # an independent owned popup as the original window.
        return int(self.u.GetAncestor(hwnd, 2) or 0) if hwnd else 0

    def activate(self, hwnd, timeout=1, clock=time.monotonic, sleep=time.sleep):
        """Restore and request foreground once; never inject input to acquire it."""
        state = self.interactive_state()
        if not state["interactive"]:
            raise RuntimeError(state["reason"])
        if self.foreground() == hwnd and not self.u.IsIconic(hwnd):
            return "already_foreground"
        if self.modifiers_down():
            raise RuntimeError("user_key_or_mouse_button_held")
        deadline = clock() + timeout
        if self.u.IsIconic(hwnd):
            self.u.ShowWindowAsync(hwnd, 9)  # SW_RESTORE, target thread owns it.
            while self.u.IsIconic(hwnd) and clock() < deadline:
                sleep(.01)
            if self.u.IsIconic(hwnd):
                raise RuntimeError("window_restore_timeout")
        self.u.BringWindowToTop(hwnd)
        self.u.SetForegroundWindow(hwnd)
        while self.foreground() != hwnd and clock() < deadline:
            sleep(.01)
        if self.foreground() == hwnd:
            return "foreground_api"
        raise RuntimeError("foreground_activation_denied")

    def interactive_state(self):
        from .session import interactive_state
        return interactive_state()

    def owned_by(self, hwnd, root):
        seen = set()
        while hwnd and hwnd not in seen:
            if hwnd == root:
                return True
            seen.add(hwnd)
            hwnd = int(self.u.GetWindow(hwnd, 4) or 0)  # GW_OWNER
        return False

    def modifiers_down(self):
        return any(self.u.GetAsyncKeyState(k) & 0x8000 for k in (0x10, 0x11, 0x12, 0x5B, 0x5C, 0x01, 0x02))

    def cursor(self):
        p = W.POINT()
        if not self.u.GetPhysicalCursorPos(C.byref(p)):
            raise C.WinError(C.get_last_error())
        return p.x, p.y

    def windows(self):
        result = []
        callback_type = C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
        @callback_type
        def visit(hwnd, _):
            if self.u.IsWindowVisible(hwnd):
                length = self.u.GetWindowTextLengthW(hwnd)
                text = C.create_unicode_buffer(length + 1)
                self.u.GetWindowTextW(hwnd, text, length + 1)
                try:
                    result.append((self.identity(hwnd), text.value))
                except OSError:
                    pass
                except RuntimeError:
                    pass
            return True
        self.u.EnumWindows.argtypes = [callback_type, W.LPARAM]
        self.u.EnumWindows.restype = W.BOOL
        if not self.u.EnumWindows(visit, 0):
            raise C.WinError(C.get_last_error())
        return result

    def click_events(self, point):
        x, y = sendinput_absolute(*point, self.desktop())
        return [INPUT(type=0, mi=MOUSEINPUT(dx=x, dy=y, dwFlags=0xC001)),
                INPUT(type=0, mi=MOUSEINPUT(dwFlags=0x0002)),
                INPUT(type=0, mi=MOUSEINPUT(dwFlags=0x0004))]

    def text_events(self, text):
        raw = text.encode("utf-16-le")
        events = []
        for i in range(0, len(raw), 2):
            unit = int.from_bytes(raw[i:i+2], "little")
            events.extend([INPUT(type=1, ki=KEYBDINPUT(wScan=unit, dwFlags=4)),
                           INPUT(type=1, ki=KEYBDINPUT(wScan=unit, dwFlags=6))])
        return events

    def send(self, events):
        values = (INPUT * len(events))(*events)
        accepted = int(self.u.SendInput(len(events), values, C.sizeof(INPUT)))
        # Release a possibly held injected key/button after partial acceptance.
        # These are release-only cleanup events, never a retry of the action.
        cleanup = []
        if 0 < accepted < len(events):
            for event in events:
                if event.type == 0 and event.mi.dwFlags in (0x0004, 0x0010, 0x0040):
                    cleanup.append(event)
                elif event.type == 1 and event.ki.dwFlags & 2:
                    cleanup.append(event)
        cleanup_accepted = None
        if cleanup:
            release = (INPUT * len(cleanup))(*cleanup)
            cleanup_accepted = int(self.u.SendInput(len(cleanup), release, C.sizeof(INPUT)))
        return accepted, len(events), cleanup_accepted
