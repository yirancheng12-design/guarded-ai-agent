import ctypes as C
import os
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import patch


@unittest.skipUnless(os.name == "nt", "Windows session ABI")
class SessionTests(unittest.TestCase):
    def read_buffer(self, flag, state=0, level=1, size=232):
        from ctypes import wintypes as W
        from guarded_agent.session import session_state
        # SDK layout: Level at 0, aligned union at 8, state at 12, flags at 16.
        data = C.create_string_buffer(232)
        struct.pack_into("<I", data, 0, level)
        struct.pack_into("<IIi", data, 8, 1, state, flag)
        freed = []

        class Function:
            def __init__(self, callback):
                self.callback = callback
            def __call__(self, *args):
                return self.callback(*args)

        def query(server, session, kind, pointer, length):
            self.assertEqual((session, kind), (0xFFFFFFFF, 25))
            C.cast(pointer, C.POINTER(C.c_void_p))[0] = C.addressof(data)
            C.cast(length, C.POINTER(W.DWORD))[0] = size
            return True

        dll = SimpleNamespace(WTSQuerySessionInformationW=Function(query), WTSFreeMemory=Function(lambda p: freed.append(p.value)))
        with patch("guarded_agent.session.C.WinDLL", return_value=dll):
            result = session_state()
        self.assertEqual(freed, [C.addressof(data)])
        return result

    def test_locked_session_is_not_interactive(self):
        result = self.read_buffer(0)
        self.assertEqual((result["interactive"], result["reason"]), (False, "session_locked"))

    def test_unlocked_active_session_is_interactive(self):
        self.assertTrue(self.read_buffer(1)["interactive"])

    def test_disconnected_session_is_not_interactive(self):
        self.assertEqual(self.read_buffer(1, state=4)["reason"], "session_not_active")

    def test_unknown_lock_state_fails_closed(self):
        self.assertFalse(self.read_buffer(-1)["interactive"])

    def test_short_native_buffer_fails_closed_and_is_freed(self):
        self.assertEqual(self.read_buffer(1, size=20)["reason"], "session_information_incomplete")

    def test_unknown_native_level_fails_closed_and_is_freed(self):
        self.assertEqual(self.read_buffer(1, level=2)["reason"], "unsupported_session_information_level")

    def test_guard_rejects_lock_before_reading_uia(self):
        from guarded_agent.windows import WindowsBackend
        backend = WindowsBackend.__new__(WindowsBackend)
        backend.api = SimpleNamespace(interactive_state=lambda: {"interactive": False, "reason": "session_locked"})
        result = backend.guard(None, observation=True)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "session_locked")

    def test_activation_rejects_lock_even_with_matching_foreground(self):
        from guarded_agent.winapi import WinAPI
        api = WinAPI.__new__(WinAPI)
        api.interactive_state = lambda: {"interactive": False, "reason": "session_locked"}
        api.foreground = lambda: 42
        with self.assertRaisesRegex(RuntimeError, "session_locked"):
            api.activate(42)

    def test_minimized_window_restored_before_activation(self):
        from guarded_agent.winapi import WinAPI
        from guarded_agent.simulation import FakeClock
        api = WinAPI.__new__(WinAPI)
        clock, calls = FakeClock(), []
        foreground = [99]
        minimized = [True]
        api.interactive_state = lambda: {"interactive": True}
        api.foreground = lambda: foreground[0]
        api.modifiers_down = lambda: False
        def activate(hwnd):
            calls.append(("activate", hwnd))
            foreground[0] = hwnd
        def restore(hwnd, command):
            calls.append(("restore", command))
            minimized[0] = False
        api.u = SimpleNamespace(IsIconic=lambda hwnd: minimized[0],
                                ShowWindowAsync=restore,
                                BringWindowToTop=lambda hwnd: calls.append(("top", hwnd)), SetForegroundWindow=activate)
        self.assertEqual(api.activate(42, clock=clock, sleep=clock.sleep), "foreground_api")
        self.assertEqual(calls, [("restore", 9), ("top", 42), ("activate", 42)])

    def test_activation_denial_does_not_loop_sending_requests(self):
        from guarded_agent.winapi import WinAPI
        from guarded_agent.simulation import FakeClock
        api = WinAPI.__new__(WinAPI)
        clock, calls = FakeClock(), []
        api.interactive_state = lambda: {"interactive": True}
        api.foreground = lambda: 99
        api.modifiers_down = lambda: False
        api.u = SimpleNamespace(IsIconic=lambda hwnd: False,
                                BringWindowToTop=lambda hwnd: None,
                                SetForegroundWindow=lambda hwnd: calls.append(hwnd))
        with self.assertRaisesRegex(RuntimeError, "foreground_activation_denied"):
            api.activate(42, .1, clock=clock, sleep=clock.sleep)
        self.assertEqual(calls, [42])
