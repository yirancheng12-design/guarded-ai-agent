"""Read-only interactive-session diagnostics. Never unlocks or switches desktops."""
import ctypes as C
from ctypes import wintypes as W


class WTSINFOEX_LEVEL1(C.Structure):
    _fields_ = [("SessionId", W.DWORD), ("SessionState", C.c_int), ("SessionFlags", W.LONG),
                ("WinStationName", W.WCHAR * 33), ("UserName", W.WCHAR * 21), ("DomainName", W.WCHAR * 18),
                ("LogonTime", C.c_longlong), ("ConnectTime", C.c_longlong),
                ("DisconnectTime", C.c_longlong), ("LastInputTime", C.c_longlong),
                ("CurrentTime", C.c_longlong), ("IncomingBytes", W.DWORD), ("OutgoingBytes", W.DWORD),
                ("IncomingFrames", W.DWORD), ("OutgoingFrames", W.DWORD),
                ("IncomingCompressedBytes", W.DWORD), ("OutgoingCompressedBytes", W.DWORD)]


class WTSINFOEX(C.Structure):
    _fields_ = [("Level", W.DWORD), ("Data", WTSINFOEX_LEVEL1)]


def session_state():
    """Windows 10+ WTS session state; personal/session names are not returned."""
    wts = C.WinDLL("wtsapi32", use_last_error=True)
    wts.WTSQuerySessionInformationW.argtypes = [W.HANDLE, W.DWORD, C.c_int, C.POINTER(C.c_void_p), C.POINTER(W.DWORD)]
    wts.WTSQuerySessionInformationW.restype = W.BOOL
    wts.WTSFreeMemory.argtypes = [C.c_void_p]
    data, size = C.c_void_p(), W.DWORD()
    if not wts.WTSQuerySessionInformationW(None, 0xFFFFFFFF, 25, C.byref(data), C.byref(size)):
        return {"interactive": False, "reason": "session_query_failed", "error": C.get_last_error()}
    try:
        if size.value < C.sizeof(WTSINFOEX):
            return {"interactive": False, "reason": "session_information_incomplete", "bytes": size.value}
        info = C.cast(data, C.POINTER(WTSINFOEX)).contents
        if info.Level != 1:
            return {"interactive": False, "reason": "unsupported_session_information_level"}
        state, flags = int(info.Data.SessionState), int(info.Data.SessionFlags)
        reason = "session_locked" if flags == 0 else "session_not_active" if state != 0 else "session_lock_state_unknown" if flags != 1 else ""
        return {"interactive": not reason, "reason": reason, "session_id": int(info.Data.SessionId),
                "connection_state": state, "lock_flag": flags}
    finally:
        wts.WTSFreeMemory(data)


def interactive_state():
    state = session_state()
    if not state["interactive"]:
        return state
    u, k = C.WinDLL("user32", use_last_error=True), C.WinDLL("kernel32", use_last_error=True)
    k.GetCurrentThreadId.restype = W.DWORD
    u.GetThreadDesktop.argtypes, u.GetThreadDesktop.restype = [W.DWORD], W.HANDLE
    u.OpenInputDesktop.argtypes, u.OpenInputDesktop.restype = [W.DWORD, W.BOOL, W.DWORD], W.HANDLE
    u.CloseDesktop.argtypes = [W.HANDLE]
    u.GetUserObjectInformationW.argtypes = [W.HANDLE, C.c_int, C.c_void_p, W.DWORD, C.POINTER(W.DWORD)]
    u.GetUserObjectInformationW.restype = W.BOOL

    def name(handle):
        text, required = C.create_unicode_buffer(256), W.DWORD()
        if not handle or not u.GetUserObjectInformationW(handle, 2, text, C.sizeof(text), C.byref(required)):
            raise OSError(C.get_last_error(), "desktop_query_failed")
        return text.value

    desktop = u.OpenInputDesktop(0, False, 1)  # DESKTOP_READOBJECTS, no switching rights.
    if not desktop:
        return {**state, "interactive": False, "reason": "input_desktop_unavailable", "error": C.get_last_error()}
    try:
        same = name(desktop) == name(u.GetThreadDesktop(k.GetCurrentThreadId()))
        return {**state, "interactive": same, "reason": "" if same else "input_desktop_changed"}
    except OSError:
        return {**state, "interactive": False, "reason": "desktop_query_failed"}
    finally:
        u.CloseDesktop(desktop)
