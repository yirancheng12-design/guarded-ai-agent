import ctypes
import os
import unittest


@unittest.skipUnless(os.name == "nt", "Win32 ABI tests")
class WinAPITests(unittest.TestCase):
    def setUp(self):
        from guarded_agent.winapi import WinAPI
        # No DLL calls; test event construction and receipts with an injected API.
        self.api = WinAPI.__new__(WinAPI)

    def test_input_struct_has_correct_native_size(self):
        from guarded_agent.winapi import INPUT
        self.assertEqual(ctypes.sizeof(INPUT), 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)

    def test_unicode_text_preserves_surrogate_pairs(self):
        events = self.api.text_events("A中😀")
        self.assertEqual(len(events), 8)
        self.assertEqual([e.ki.wScan for e in events[::2]], [0x41, 0x4E2D, 0xD83D, 0xDE00])
        self.assertTrue(all(e.ki.dwFlags == 6 for e in events[1::2]))

    def test_click_uses_absolute_virtual_desktop_coordinates(self):
        from guarded_agent.model import Rect
        self.api.desktop = lambda: Rect(-100, 0, 201, 100)
        events = self.api.click_events((0, 50))
        self.assertEqual(events[0].mi.dwFlags, 0xC001)
        self.assertEqual(events[0].mi.dx, 32768)
        self.assertEqual([e.mi.dwFlags for e in events[1:]], [2, 4])

    def test_partial_send_only_cleans_up_releases(self):
        from types import SimpleNamespace
        calls = []
        def send(count, inputs, size):
            calls.append([(inputs[i].type, inputs[i].ki.dwFlags) for i in range(count)])
            return 1 if len(calls) == 1 else count
        self.api.u = SimpleNamespace(SendInput=send)
        accepted, requested, cleanup = self.api.send(self.api.text_events("ab"))
        self.assertEqual((accepted, requested, cleanup), (1, 4, 2))
        self.assertEqual(calls[1], [(1, 6), (1, 6)])

    def test_zero_accepted_does_not_issue_extra_input(self):
        from types import SimpleNamespace
        calls = []
        self.api.u = SimpleNamespace(SendInput=lambda n, arr, size: calls.append(n) or 0)
        self.assertEqual(self.api.send(self.api.text_events("x")), (0, 2, None))
        self.assertEqual(calls, [2])

    def test_foreground_child_is_bound_to_its_parent_root(self):
        from types import SimpleNamespace
        calls = []
        self.api.u = SimpleNamespace(GetForegroundWindow=lambda: 101,
            GetAncestor=lambda hwnd, flag: calls.append((hwnd, flag)) or 42)
        self.assertEqual(self.api.foreground(), 42)
        self.assertEqual(calls, [(101, 2)])

    def test_owned_popup_does_not_normalize_to_its_owner(self):
        from types import SimpleNamespace
        self.api.u = SimpleNamespace(GetForegroundWindow=lambda: 77,
            GetAncestor=lambda hwnd, flag: 42 if flag == 3 else 77)
        self.assertEqual(self.api.foreground(), 77)

    def test_absent_or_destroyed_foreground_is_not_accepted(self):
        from types import SimpleNamespace
        self.api.u = SimpleNamespace(GetForegroundWindow=lambda: 0,
            GetAncestor=lambda hwnd, flag: self.fail('Must not query null foreground'))
        self.assertEqual(self.api.foreground(), 0)
        self.api.u = SimpleNamespace(GetForegroundWindow=lambda: 101, GetAncestor=lambda hwnd, flag: 0)
        self.assertEqual(self.api.foreground(), 0)
