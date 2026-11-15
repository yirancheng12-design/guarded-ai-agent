"""Real UIA checks against our own off-screen, nonactivating Win32 fixture.

No activation, global keystrokes, mouse events or user-application writes.
"""
import importlib.util
import multiprocessing as mp
import os
import time
import unittest
from guarded_agent.model import Action, Condition, Selector
from guarded_agent.isolation import ProcessBackend
from guarded_agent.windows import WindowsBackend


class ReadOnlyBackend(WindowsBackend):
    def read_condition(self, action, timeout=2):
        return self._read(action)[0]


def fixture_process(pipe):
    import ctypes as C
    from ctypes import wintypes as W
    from guarded_agent.winapi import WinAPI
    api = WinAPI()
    api.initialize_dpi()
    u = api.u
    api.k.GetModuleHandleW.argtypes = [W.LPCWSTR]
    api.k.GetModuleHandleW.restype = W.HMODULE
    instance = api.k.GetModuleHandleW(None)
    wndproc_type = C.WINFUNCTYPE(C.c_ssize_t, W.HWND, W.UINT, W.WPARAM, W.LPARAM)
    u.DefWindowProcW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
    u.DefWindowProcW.restype = C.c_ssize_t
    @wndproc_type
    def wndproc(hwnd, message, wparam, lparam):
        return u.DefWindowProcW(hwnd, message, wparam, lparam)
    class WNDCLASS(C.Structure):
        _fields_ = [("style", W.UINT), ("lpfnWndProc", wndproc_type),
                    ("cbClsExtra", C.c_int), ("cbWndExtra", C.c_int),
                    ("hInstance", W.HINSTANCE), ("hIcon", W.HANDLE),
                    ("hCursor", W.HANDLE), ("hbrBackground", W.HANDLE),
                    ("lpszMenuName", W.LPCWSTR), ("lpszClassName", W.LPCWSTR)]
    wc = WNDCLASS(lpfnWndProc=wndproc, hInstance=instance, lpszClassName="GuardedAgentReadOnlyFixture")
    u.RegisterClassW.argtypes = [C.POINTER(WNDCLASS)]
    u.RegisterClassW.restype = W.WORD
    if not u.RegisterClassW(C.byref(wc)):
        raise C.WinError(C.get_last_error())
    u.CreateWindowExW.argtypes = [W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD,
                                 C.c_int, C.c_int, C.c_int, C.c_int,
                                 W.HWND, W.HMENU, W.HINSTANCE, C.c_void_p]
    u.CreateWindowExW.restype = W.HWND
    u.SendMessageW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
    u.SendMessageW.restype = C.c_ssize_t
    u.DestroyWindow.argtypes = [W.HWND]
    u.DestroyWindow.restype = W.BOOL
    u.PeekMessageW.argtypes = [C.POINTER(W.MSG), W.HWND, W.UINT, W.UINT, W.UINT]
    u.PeekMessageW.restype = W.BOOL
    u.TranslateMessage.argtypes = [C.POINTER(W.MSG)]
    u.DispatchMessageW.argtypes = [C.POINTER(W.MSG)]
    u.DispatchMessageW.restype = C.c_ssize_t
    root = u.CreateWindowExW(0x08000080, "GuardedAgentReadOnlyFixture", "Guarded Agent Test Fixture", 0x10CF0000,
                             -30000, -30000, 400, 240, None, None, instance, None)
    if not root:
        pipe.send({"error": str(C.WinError(C.get_last_error()))})
        return
    try:
        edit = u.CreateWindowExW(0, "EDIT", "0", 0x50010000, 10, 10, 160, 30, root, 101, instance, None)
        toggle = u.CreateWindowExW(0, "BUTTON", "Choice", 0x50010003, 10, 50, 160, 30, root, 102, instance, None)
        u.CreateWindowExW(0, "STATIC", "Duplicate", 0x50000000, 10, 90, 160, 25, root, 103, instance, None)
        u.CreateWindowExW(0, "STATIC", "Duplicate", 0x50000000, 10, 120, 160, 25, root, 104, instance, None)
        if not edit or not toggle:
            raise C.WinError(C.get_last_error())
        u.SendMessageW(toggle, 0x00F1, 1, 0)  # Fixture initializes its own checkbox.
        pipe.send(api.identity(root))
        message = W.MSG()
        while not pipe.poll():
            while u.PeekMessageW(C.byref(message), None, 0, 0, 1):
                u.TranslateMessage(C.byref(message))
                u.DispatchMessageW(C.byref(message))
            time.sleep(0.005)
    finally:
        u.DestroyWindow(root)
        pipe.close()


@unittest.skipUnless(os.name == "nt" and importlib.util.find_spec("comtypes"), "Real Windows UIA dependency required")
class RealUIAReadOnlyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        context = mp.get_context("spawn")
        cls.pipe, child = context.Pipe()
        cls.fixture = context.Process(target=fixture_process, args=(child,), daemon=True)
        cls.fixture.start()
        child.close()
        if not cls.pipe.poll(10):
            cls.fixture.terminate()
            cls.fixture.join(1)
            raise RuntimeError("fixture_startup_timeout")
        cls.window = cls.pipe.recv()
        if isinstance(cls.window, dict):
            raise RuntimeError(cls.window["error"])
        cls.backend = ProcessBackend(factory=ReadOnlyBackend)

    @classmethod
    def tearDownClass(cls):
        cls.backend.close()
        cls.pipe.send("close")
        cls.fixture.join(2)
        if cls.fixture.is_alive():
            cls.fixture.terminate()
            cls.fixture.join(1)
        cls.pipe.close()

    def read(self, selector, property, expected):
        a = Action("read-only", self.window, Selector(control_type=50004), "click",
                   Condition(selector, property, expected))
        return self.backend.read_condition(a, timeout=3)

    def test_value_pattern_reads_actual_edit_control(self):
        evidence = self.read(Selector(control_type=50004), "value", "0")
        self.assertEqual((evidence.status, evidence.value), ("valid", "0"))

    def test_native_worker_uses_mta_and_physical_dpi(self):
        probe = self.backend.probe(timeout=3)
        self.assertEqual(probe["uia_apartment"], "MTA")
        self.assertEqual(probe["dpi_mode"], "PerMonitorV2")

    def test_toggle_pattern_reads_actual_checkbox(self):
        evidence = self.read(Selector(name="Choice"), "toggle", 1)
        self.assertEqual((evidence.status, evidence.value), ("valid", 1))

    def test_absent_element_is_valid_existence_evidence(self):
        evidence = self.read(Selector(name="Not present"), "exists", False)
        self.assertEqual((evidence.status, evidence.value), ("valid", False))

    def test_ambiguous_selector_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "selector_match_count:2"):
            self.read(Selector(name="Duplicate"), "name", "Duplicate")

    def test_unsupported_pattern_is_distinguished_from_rpc_failure(self):
        evidence = self.read(Selector(name="Choice"), "value", "x")
        self.assertEqual(evidence.status, "unsupported")

    def test_model_snapshot_contains_current_value_and_toggle_state(self):
        snapshot = self.backend.inspect(self.window.hwnd, timeout=5)
        edits = [item for item in snapshot["elements"] if item["control_type"] == 50004]
        choices = [item for item in snapshot["elements"] if item["name"] == "Choice"]
        self.assertEqual(edits[0]["value"], "0")
        self.assertEqual(choices[0]["toggle"], 1)
