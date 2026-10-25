"""Opt-in integration acceptance: real Ollama, UIA and SendInput in our own app.

Run from the project directory on an unlocked Windows desktop:
    python examples/verify_live.py --model gemma4:26b --output live-verification

This opens a disposable test window and briefly uses the mouse and keyboard.
It never targets an existing user application. Not part of unittest discovery.
"""
from dataclasses import asdict, replace
from datetime import datetime, timezone
import argparse
import ctypes as C
from ctypes import wintypes as W
import json
import multiprocessing as mp
from pathlib import Path
import sys
import time
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from guarded_agent.engine import Executor
from guarded_agent.isolation import ProcessBackend
from guarded_agent.journal import Journal
from guarded_agent.model import Action, Condition, Policy, Selector, VisualSpec
from guarded_agent.ollama_agent import OllamaAgent, OllamaClient
from guarded_agent.winapi import WinAPI
from guarded_agent.session import interactive_state


def fixture_process(pipe):
    """The test application owns its controls; only the production backend inputs."""
    api = WinAPI()
    api.initialize_dpi()
    u = api.u
    api.k.GetModuleHandleW.argtypes = [W.LPCWSTR]
    api.k.GetModuleHandleW.restype = W.HMODULE
    instance = api.k.GetModuleHandleW(None)
    proc_type = C.WINFUNCTYPE(C.c_ssize_t, W.HWND, W.UINT, W.WPARAM, W.LPARAM)
    u.DefWindowProcW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
    u.DefWindowProcW.restype = C.c_ssize_t
    u.CreateWindowExW.argtypes = [W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD,
                                C.c_int, C.c_int, C.c_int, C.c_int,
                                W.HWND, W.HMENU, W.HINSTANCE, C.c_void_p]
    u.CreateWindowExW.restype = W.HWND
    u.SetWindowTextW.argtypes = [W.HWND, W.LPCWSTR]
    u.GetWindowTextW.argtypes = [W.HWND, W.LPWSTR, C.c_int]
    u.GetWindowTextW.restype = C.c_int
    u.SendMessageW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
    u.SendMessageW.restype = C.c_ssize_t
    u.SetFocus.argtypes = [W.HWND]
    u.SetFocus.restype = W.HWND
    u.ShowWindow.argtypes = [W.HWND, C.c_int]
    u.ShowWindow.restype = W.BOOL
    u.UpdateWindow.argtypes = [W.HWND]
    u.UpdateWindow.restype = W.BOOL
    u.DestroyWindow.argtypes = [W.HWND]
    u.DestroyWindow.restype = W.BOOL
    u.PeekMessageW.argtypes = [C.POINTER(W.MSG), W.HWND, W.UINT, W.UINT, W.UINT]
    u.PeekMessageW.restype = W.BOOL
    u.TranslateMessage.argtypes = [C.POINTER(W.MSG)]
    u.DispatchMessageW.argtypes = [C.POINTER(W.MSG)]
    u.DispatchMessageW.restype = C.c_ssize_t
    controls, click_events = {}, []
    display, invoked, popup = "0", 0, None

    @proc_type
    def wndproc(hwnd, message, wparam, lparam):
        nonlocal display, invoked, popup
        if message == 0x111 and lparam and (wparam >> 16) == 0:  # WM_COMMAND / BN_CLICKED
            control_id = wparam & 0xFFFF
            if control_id in {201, 202, 301, 401, 402, 403}:
                click_events.append({"control_id": control_id, "at": time.monotonic()})
            if control_id in {201, 202}:
                digit = str(control_id - 200)
                display = digit if display == "0" else display + digit
                u.SetWindowTextW(controls[101], display)
            elif control_id == 401:
                invoked += 1
                u.SetWindowTextW(controls[104], f"Invoked {invoked}")
            elif control_id == 403:
                # Intentional application transition to exercise lost-context handling.
                popup = u.CreateWindowExW(0, "STATIC", "Fixture focus-loss probe", 0x10CF0000,
                                         190, 200, 360, 160, hwnd, None, instance, None)
                u.SetForegroundWindow(popup)
                u.SetFocus(popup)
            return 0
        return u.DefWindowProcW(hwnd, message, wparam, lparam)

    class WNDCLASS(C.Structure):
        _fields_ = [("style", W.UINT), ("lpfnWndProc", proc_type),
                    ("cbClsExtra", C.c_int), ("cbWndExtra", C.c_int),
                    ("hInstance", W.HINSTANCE), ("hIcon", W.HANDLE),
                    ("hCursor", W.HANDLE), ("hbrBackground", W.HANDLE),
                    ("lpszMenuName", W.LPCWSTR), ("lpszClassName", W.LPCWSTR)]
    wc = WNDCLASS(lpfnWndProc=wndproc, hInstance=instance,
                  hbrBackground=6, lpszClassName="GuardedAgentLiveFixture")
    u.RegisterClassW.argtypes = [C.POINTER(WNDCLASS)]
    u.RegisterClassW.restype = W.WORD
    if not u.RegisterClassW(C.byref(wc)):
        raise C.WinError(C.get_last_error())
    root = u.CreateWindowExW(0, wc.lpszClassName, "Guarded Agent - live acceptance fixture",
                             0x10CF0000, 120, 120, 560, 410, None, None, instance, None)
    if not root:
        raise C.WinError(C.get_last_error())

    def add(control_id, kind, text, x, y, width, height, extra=0):
        hwnd = u.CreateWindowExW(0, kind, text, 0x50010000 | extra,
                                 x, y, width, height, root, control_id, instance, None)
        if not hwnd:
            raise C.WinError(C.get_last_error())
        controls[control_id] = hwnd

    def read(hwnd):
        buf = C.create_unicode_buffer(1024)
        u.GetWindowTextW(hwnd, buf, len(buf))
        return buf.value

    try:
        add(100, "STATIC", "Disposable test app: calculator, Unicode, toggle and stop guards", 15, 15, 500, 28)
        add(101, "EDIT", "0", 15, 50, 300, 35, 0x800 | 0x800000)  # readonly display
        add(201, "BUTTON", "1", 15, 100, 90, 45)
        add(202, "BUTTON", "2", 120, 100, 90, 45)
        add(103, "EDIT", "", 15, 165, 300, 35, 0x800000)
        add(301, "BUTTON", "Choice", 335, 165, 160, 35, 3)  # auto checkbox
        add(401, "BUTTON", "Invoke", 15, 220, 90, 40)
        add(104, "STATIC", "Not invoked", 125, 225, 220, 35)
        add(402, "BUTTON", "No result", 15, 280, 130, 40)
        add(403, "BUTTON", "Change focus", 165, 280, 160, 40)
        # The fixture shows its own startup window; no user window is targeted.
        u.ShowWindow(root, 5)
        u.UpdateWindow(root)
        u.SetForegroundWindow(root)
        pipe.send({"window": api.identity(root), "dpi": int(u.GetDpiForWindow(root)),
                   "rect": asdict(api.rect(root)), "desktop": asdict(api.desktop())})
        message = W.MSG()
        while True:
            while u.PeekMessageW(C.byref(message), None, 0, 0, 1):
                u.TranslateMessage(C.byref(message))
                u.DispatchMessageW(C.byref(message))
            if pipe.poll():
                command = pipe.recv()
                if command["kind"] == "close":
                    break
                if command["kind"] == "state":
                    pipe.send({"display": read(controls[101]), "text": read(controls[103]),
                               "toggle": int(u.SendMessageW(controls[301], 0xF0, 0, 0)),
                               "invoked": invoked, "click_events": list(click_events),
                               "foreground_is_fixture": api.foreground() == root})
                elif command["kind"] == "capture":
                    from PIL import ImageGrab
                    destination = Path(command["path"])
                    ImageGrab.grab(bbox=api.rect(root).bbox, all_screens=True).save(destination)
                    template = destination.with_name("display-template.png")
                    ImageGrab.grab(bbox=api.rect(controls[101]).bbox, all_screens=True).save(template)
                    pipe.send({"screenshot": str(destination), "template": str(template)})
            time.sleep(.005)
    finally:
        if popup:
            u.DestroyWindow(popup)
        u.DestroyWindow(root)
        pipe.close()


class Fixture:
    def __init__(self, wait_for_foreground=False):
        self.wait_for_foreground = wait_for_foreground

    def __enter__(self):
        context = mp.get_context("spawn")
        self.pipe, child = context.Pipe()
        self.process = context.Process(target=fixture_process, args=(child,), daemon=True)
        self.process.start()
        child.close()
        if not self.pipe.poll(15):
            self.process.terminate()
            self.process.join(2)
            raise RuntimeError("fixture_startup_timeout")
        self.info = self.pipe.recv()
        self.window = self.info["window"]
        if self.wait_for_foreground:
            print(json.dumps({"waiting_for_foreground": self.window.hwnd,
                              "title": "Guarded Agent - live acceptance fixture"}), flush=True)
            deadline = time.monotonic() + 600
            while not self.call("state")["foreground_is_fixture"]:
                if time.monotonic() >= deadline:
                    self.__exit__()
                    raise RuntimeError("foreground_setup_timeout")
                time.sleep(.2)
        return self

    def call(self, kind, **kwargs):
        self.pipe.send({"kind": kind, **kwargs})
        if not self.pipe.poll(5):
            raise RuntimeError("fixture_read_timeout")
        return self.pipe.recv()

    def __exit__(self, *args):
        if self.process.is_alive():
            self.pipe.send({"kind": "close"})
        self.process.join(3)
        if self.process.is_alive():
            self.process.terminate()
            self.process.join(2)
        self.pipe.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--wait-for-foreground", action="store_true",
                        help="Allow up to 600s for the operator to activate each test window")
    parser.add_argument("--wait-for-unlock", type=int, default=0,
                        help="Wait up to this many seconds for an unlocked interactive session; no window or input while locked")
    args = parser.parse_args()
    if not 0 <= args.wait_for_unlock <= 3600:
        parser.error("--wait-for-unlock must be between 0 and 3600 seconds")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)  # Never reuse a run's journal.
    report = {"started": datetime.now(timezone.utc).isoformat(), "model": args.model,
              "fixture": "visible native Win32 app", "checks": [], "success": False,
              "operator_foreground_setup": args.wait_for_foreground}

    def check(name, passed, evidence):
        report["checks"].append({"name": name, "passed": bool(passed), "evidence": evidence})
        print(json.dumps({"check": name, "passed": bool(passed)}, ensure_ascii=False), flush=True)
        (output / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if not passed:
            raise AssertionError(name)

    try:
        session = interactive_state()
        report["initial_session"] = session
        if not session["interactive"] and args.wait_for_unlock:
            report["status"] = "waiting_for_unlock"
            (output / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(json.dumps({"waiting_for_unlock": session["reason"], "timeout_seconds": args.wait_for_unlock}), flush=True)
            deadline = time.monotonic() + args.wait_for_unlock
            while not session["interactive"] and time.monotonic() < deadline:
                time.sleep(.5)
                session = interactive_state()
        report["session"] = session
        if not session["interactive"]:
            raise RuntimeError(session["reason"] + "; unlock the Windows desktop before running live acceptance")
        report["status"] = "running"
        report["policy"] = asdict(Policy())
        (output / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        with Fixture(args.wait_for_foreground) as fixture:
            report["environment"] = {**fixture.info, "window": asdict(fixture.window)}
            journal = Journal(output / "actions.sqlite3")
            try:
                with ProcessBackend(evidence_dir=str(output / "frames")) as backend:
                    runner = Executor(backend, journal, Policy())
                    display = Selector(automation_id="101")
                    catalog = {
                        "press_1": Action("1", fixture.window, Selector(automation_id="201"), "click", Condition(display, "value", "1")),
                        "press_2": Action("2", fixture.window, Selector(automation_id="202"), "click", Condition(display, "value", "12")),
                    }
                    probe = replace(catalog["press_2"], operation_id="goal")
                    agent = OllamaAgent(OllamaClient(args.model), runner, max_steps=3)
                    result = agent.run("Starting from 0, enter 12 by pressing 1 then 2. Stop when display is 12.",
                                       fixture.window, catalog, probe, "live-ollama")
                    state = fixture.call("state")
                    report["ollama"] = result
                    check("ollama_real_clicks", result["outcome"] == "success" and state["display"] == "12"
                          and [e["control_id"] for e in state["click_events"]] == [201, 202], state)

                    text = "Ollama 中文 🙂"
                    action = Action("unicode", fixture.window, Selector(automation_id="103"), "type_text",
                                    Condition(Selector(automation_id="103"), "value", text), text=text)
                    result = runner.run(action)
                    check("unicode_keyboard_and_focus", result.outcome == "success" and fixture.call("state")["text"] == text,
                          result.to_dict())

                    toggle = Action("toggle", fixture.window, Selector(automation_id="301"), "toggle",
                                    Condition(Selector(automation_id="301"), "toggle", 1))
                    result = runner.run(toggle)
                    check("uia_toggle_pattern", result.outcome == "success" and fixture.call("state")["toggle"] == 1, result.to_dict())

                    invoke = Action("invoke", fixture.window, Selector(automation_id="401"), "invoke",
                                    Condition(Selector(automation_id="104"), "name", "Invoked 1"), intent="command")
                    result = runner.run(invoke)
                    state = fixture.call("state")
                    check("uia_invoke_pattern", result.outcome == "success" and state["invoked"] == 1,
                          {"result": result.to_dict(), "state": state})
                    repeated = runner.run(invoke)
                    check("completed_operation_not_replayed", repeated == result and fixture.call("state")["invoked"] == 1, repeated.to_dict())

                    images = fixture.call("capture", path=str(output / "successful-state.png"))
                    visual = replace(probe, operation_id="visual-stability", condition=Condition(display, "value", "12", mode="both",
                                     visual=VisualSpec(template=images["template"])))
                    result = runner.run(visual)
                    check("fresh_uia_visual_samples", result.outcome == "success" and result.execution_status == "not_sent"
                          and result.samples >= 3, result.to_dict())

                    noop = Action("unknown-once", fixture.window, Selector(automation_id="402"), "click",
                                  Condition(Selector(automation_id="104"), "name", "Completed"), intent="command")
                    result = runner.run(noop)
                    check("no_result_becomes_unknown", result.execution_status == "sent" and result.outcome == "unknown"
                          and result.needs_review, result.to_dict())
                    report["before_restart"] = fixture.call("state")
                    report["dispatch_receipts"] = [{"operation_id": op, **json.loads(data)} for op, data in
                                                   journal.db.execute("SELECT operation_id,data FROM events WHERE kind='dispatch' ORDER BY id")]
                    clicks = [r for r in report["dispatch_receipts"] if r["operation_id"].startswith("live-ollama:")]
                    check("native_input_receipts", len(clicks) == 2 and all(r["accepted"] == r["requested"] == 3 for r in clicks), clicks)
            finally:
                journal.close()
            # New worker + reopened durable journal, changed ID and changed target.
            journal = Journal(output / "actions.sqlite3")
            try:
                with ProcessBackend() as backend:
                    blocked = Executor(backend, journal).run(replace(invoke, operation_id="different-id-after-restart"))
                    state = fixture.call("state")
                    check("unknown_blocks_after_restart", blocked.outcome == "unknown" and blocked.execution_status == "not_sent"
                          and blocked.reason.startswith("unresolved_effect:") and state["click_events"] == report["before_restart"]["click_events"],
                          {"result": blocked.to_dict(), "actual_clicks": state["click_events"]})
            finally:
                journal.close()

        with Fixture(args.wait_for_foreground) as fixture:
            journal = Journal(output / "focus.sqlite3")
            try:
                with ProcessBackend() as backend:
                    action = Action("focus-loss", fixture.window, Selector(automation_id="403"), "click",
                                    Condition(Selector(automation_id="101"), "value", "never"), intent="command")
                    result = Executor(backend, journal).run(action)
                    state = fixture.call("state")
                    check("post_send_focus_loss_stops", result.outcome == "unknown" and result.execution_status == "sent"
                          and "foreground_lost" in result.reason and [e["control_id"] for e in state["click_events"]] == [403],
                          {"result": result.to_dict(), "state": state})
            finally:
                journal.close()
        report["success"] = True
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["traceback"] = traceback.format_exc()
        print(report["traceback"], flush=True)
    finally:
        report["status"] = "passed" if report["success"] else "failed"
        report["finished"] = datetime.now(timezone.utc).isoformat()
        (output / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"success": report["success"], "report": str(output / "result.json")}), flush=True)
    return 0 if report["success"] else 1


if __name__ == "__main__":
    mp.freeze_support()
    raise SystemExit(main())
