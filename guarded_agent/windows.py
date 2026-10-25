from __future__ import annotations
from dataclasses import asdict
from pathlib import Path
import hashlib
import sys
import time
from .geometry import normalized_point
from .model import Context, Evidence, Observation, Receipt, Rect
from .vision import changed_ratio, image_hash, cursor_mask
from .winapi import WinAPI


class UnsupportedPattern(RuntimeError):
    pass


class WindowsBackend:
    """Instantiate only in ProcessBackend's disposable worker.

    This is a normal application backend, not a desktop-wide lock. Inputs have
    a final context check, but a shared desktop still has an unavoidable race.
    """
    def __init__(self, evidence_dir=None):
        self.api = WinAPI()
        self.api.initialize_dpi()  # Before COM imports or any window creation.
        # UIA runs on a dedicated, windowless MTA worker. comtypes otherwise
        # initializes the importing thread as STA, which needs message pumping.
        sys.coinit_flags = 0  # COINIT_MULTITHREADED, before importing comtypes.
        import comtypes.client
        import ctypes as C
        apartment, qualifier = C.c_int(), C.c_int()
        get_apartment = C.OleDLL("ole32").CoGetApartmentType
        get_apartment.argtypes = [C.POINTER(C.c_int), C.POINTER(C.c_int)]
        get_apartment.restype = C.HRESULT
        get_apartment(C.byref(apartment), C.byref(qualifier))
        if apartment.value != 1:  # APTTYPE_MTA
            raise RuntimeError("UIA_requires_fresh_MTA_worker")
        self.comtypes = comtypes
        self.m = comtypes.client.GetModule("UIAutomationCore.dll")
        self.uia = comtypes.client.CreateObject(self.m.CUIAutomation, interface=self.m.IUIAutomation)
        self.sequence = 0
        self.templates = {}
        self.previous = {}
        self.baselines = {}
        self.input_lease = None
        self.evidence_dir = Path(evidence_dir) if evidence_dir else None
        if self.evidence_dir:
            self.evidence_dir.mkdir(parents=True, exist_ok=True)

    def _identity(self, action):
        if self.api.identity(action.window.hwnd) != action.window:
            raise RuntimeError("window_or_process_identity_changed")

    def _roots(self, action, result=False):
        self._identity(action)
        roots = [action.window.hwnd]
        if result and action.allow_owned_result:
            roots += [w.hwnd for w, _ in self.api.windows()
                      if w.hwnd != action.window.hwnd and w.pid == action.window.pid
                      and w.created == action.window.created and self.api.owned_by(w.hwnd, action.window.hwnd)]
        return [self.uia.ElementFromHandle(hwnd) for hwnd in roots]

    def _select(self, action, selector, result=False, missing_ok=False):
        conditions = []
        for key, property_id in (("automation_id", 30011), ("name", 30005), ("control_type", 30003)):
            value = getattr(selector, key)
            if value is not None:
                conditions.append(self.uia.CreatePropertyCondition(property_id, value))
        condition = conditions[0]
        for item in conditions[1:]:
            condition = self.uia.CreateAndCondition(condition, item)
        matches = []
        for root in self._roots(action, result):
            items = root.FindAll(7, condition)  # TreeScope_Subtree, including root.
            matches.extend(items.GetElement(i) for i in range(items.Length))
        if not matches and missing_ok:
            return None
        if len(matches) != 1:
            raise RuntimeError(f"selector_match_count:{len(matches)}")
        return matches[0]

    @staticmethod
    def _rect(element):
        r = element.CurrentBoundingRectangle
        return Rect(int(r.left), int(r.top), int(r.right - r.left), int(r.bottom - r.top))

    @staticmethod
    def _runtime(element):
        return tuple(element.GetRuntimeId())

    def _pattern(self, element, pattern_id, interface):
        try:
            obj = element.GetCurrentPattern(pattern_id)
            if not obj:
                raise UnsupportedPattern(str(pattern_id))
            return obj.QueryInterface(interface)
        except self.comtypes.COMError as exc:
            # Never downgrade element unavailable, access denied or RPC errors.
            if exc.hresult & 0xFFFFFFFF in (0x80040204, 0x80004002):
                raise UnsupportedPattern(str(pattern_id)) from exc
            raise

    def _descendant(self, element, ancestor):
        for _ in range(64):
            if not element:
                return False
            if self.uia.CompareElements(element, ancestor):
                return True
            element = self.uia.RawViewWalker.GetParentElement(element)
        return False

    def activate_window(self, window, timeout=2):
        if self.api.identity(window.hwnd) != window:
            raise RuntimeError("window_or_process_identity_changed")
        if self.input_lease is None:
            self.input_lease = self.api.acquire_input_lease(min(timeout, 0.5))
        # Only preparation may activate. Observation never steals focus back.
        method = self.api.activate(window.hwnd, timeout=min(timeout * .6, 1))
        if self.api.identity(window.hwnd) != window:
            raise RuntimeError("window_or_process_identity_changed")
        return {"method": method, "foreground": self.api.foreground()}

    def _load_template(self, action):
        if action.condition.visual and action.operation_id not in self.templates:
            from PIL import Image
            with Image.open(action.condition.visual.template) as source:
                self.templates[action.operation_id] = source.convert("RGB").copy()

    def prepare(self, action, timeout=2):
        self._identity(action)
        self._load_template(action)
        self.activate_window(action.window, timeout)
        element = self._select(action, action.target)
        if action.kind == "type_text":
            if not element.CurrentIsKeyboardFocusable:
                raise RuntimeError("target_cannot_receive_keyboard_focus")
            element.SetFocus()
        if action.kind == "invoke":
            self._pattern(element, 10000, self.m.IUIAutomationInvokePattern)
        if action.kind == "toggle":
            self._pattern(element, 10015, self.m.IUIAutomationTogglePattern)

    def guard(self, action, observation=False, timeout=2):
        return self._guard(action, observation)

    def _guard(self, action, observation=False, element=None):
        try:
            session = self.api.interactive_state()
            if not session["interactive"]:
                return Context(False, reason=session["reason"])
            self._identity(action)
            hwnd = action.window.hwnd
            foreground = self.api.foreground()
            allowed_owned = observation and action.allow_owned_result and self.api.owned_by(foreground, hwnd)
            if foreground != hwnd and not allowed_owned:
                return Context(False, reason="foreground_lost")
            dpi = int(self.api.u.GetDpiForWindow(hwnd))
            if dpi <= 0:
                return Context(False, reason="invalid_window_dpi")
            token = (hwnd, action.window.pid, action.window.created, self.api.rect(hwnd).bbox, dpi, foreground)
            if observation:
                return Context(True, token)
            if element is None:
                element = self._select(action, action.target)
            if not element.CurrentIsEnabled or element.CurrentIsOffscreen:
                return Context(False, reason="target_disabled_or_offscreen")
            rect = self._rect(element)
            token += (self._runtime(element), rect.bbox)
            if action.kind == "type_text":
                focused = self.uia.GetFocusedElement()
                if not focused or not self.uia.CompareElements(element, focused) or not element.CurrentHasKeyboardFocus:
                    return Context(False, reason="wrong_input_focus")
            if action.kind in {"click", "type_text"} and self.api.modifiers_down():
                return Context(False, reason="user_key_or_mouse_button_held")
            if action.kind == "click":
                from ctypes.wintypes import POINT
                point = normalized_point(rect, *action.point)
                hit_hwnd = self.api.u.WindowFromPoint(POINT(*point))
                if int(self.api.u.GetAncestor(hit_hwnd, 2) or 0) != hwnd:
                    return Context(False, reason="point_over_other_window")
                hit = self.uia.ElementFromPoint(POINT(*point))
                if not self._descendant(hit, element):
                    return Context(False, reason="point_over_other_element")
            return Context(True, token)
        except Exception as exc:
            return Context(False, reason=type(exc).__name__ + ":" + str(exc))

    def _read(self, action):
        c = action.condition
        element = self._select(action, c.selector, result=True, missing_ok=True)
        if c.property == "exists":
            return Evidence("valid", element is not None), element
        if element is None:
            return Evidence("error", detail="result_element_missing"), None
        try:
            if c.property == "name":
                value = element.CurrentName
            elif c.property == "value":
                value = self._pattern(element, 10002, self.m.IUIAutomationValuePattern).CurrentValue
            elif c.property == "text":
                value = self._pattern(element, 10014, self.m.IUIAutomationTextPattern).DocumentRange.GetText(-1)
            else:
                value = int(self._pattern(element, 10015, self.m.IUIAutomationTogglePattern).CurrentToggleState)
            return Evidence("valid", value), element
        except UnsupportedPattern as exc:
            return Evidence("unsupported", detail=str(exc)), element

    def sample(self, action, timeout=2):
        start = time.monotonic()
        self._load_template(action)
        self.sequence += 1
        element = None
        try:
            uia, element = self._read(action)
        except Exception as exc:
            uia = Evidence("error", detail=type(exc).__name__ + ":" + str(exc))
        visual = Evidence("unsupported")
        region_hash, delta, path = None, None, None
        spec = action.condition.visual
        if spec:
            try:
                from PIL import ImageGrab
                roi = spec.roi or self._rect(element)
                # Fixed physical ROIs are tied to the current window geometry.
                # Geometry changes invalidate them rather than silently shifting.
                geometry = self.api.rect(action.window.hwnd).bbox
                old = self.previous.get(action.operation_id)
                if spec.roi and old and old[3] != geometry:
                    raise RuntimeError("fixed_roi_window_geometry_changed")
                image = ImageGrab.grab(bbox=roi.bbox, all_screens=True).convert("RGB")
                if image.size != (roi.width, roi.height):
                    raise RuntimeError("capture_not_in_physical_pixels")
                masks = spec.masks + (cursor_mask(*self.api.cursor(), roi),)
                mismatch = changed_ratio(self.templates[action.operation_id], image, spec.pixel_tolerance, masks)
                visual = Evidence("valid", mismatch <= spec.max_changed_ratio, f"template_mismatch={mismatch:.6f}")
                region_hash = image_hash(image)
                baseline = self.baselines.setdefault(action.operation_id, (roi, image.copy(), masks))
                if (baseline[0].width, baseline[0].height) == (roi.width, roi.height):
                    delta = changed_ratio(baseline[1], image, spec.pixel_tolerance, masks + baseline[2])
                self.previous[action.operation_id] = (roi, image.copy(), masks, geometry)
                if self.evidence_dir:
                    prefix = hashlib.sha256(action.operation_id.encode()).hexdigest()[:16]
                    path = str(self.evidence_dir / f"{prefix}-{self.sequence:05d}.png")
                    image.save(path)
            except Exception as exc:
                visual = Evidence("error", detail=type(exc).__name__ + ":" + str(exc))
        return Observation(self.sequence, start, time.monotonic(), uia, visual, region_hash, delta, path)

    def dispatch(self, action, token, timeout=2):
        if self.input_lease is None:
            return Receipt("not_sent", "input_lease_not_acquired")
        context = self._guard(action, observation=True)
        if not context.ok or context.token != token[:len(context.token)]:
            return Receipt("not_sent", "pre_resolution_guard_rejected:" + context.reason)
        # Bind once for this dispatch. Repeated subtree walks are expensive and
        # can resolve to a different object. All checks below still read CURRENT
        # properties, identity, focus, hit-test and geometry on this same object.
        element = self._select(action, action.target)
        current = self._guard(action, element=element)
        if not current.ok or current.token != token:
            return Receipt("not_sent", "final_guard_rejected:" + current.reason)
        # Re-resolving may be slow: validate identity and geometry once more.
        if self._runtime(element) != token[-2] or self._rect(element).bbox != token[-1]:
            return Receipt("not_sent", "target_changed_during_resolution")
        if action.kind in {"invoke", "toggle"}:
            try:
                if action.kind == "invoke":
                    pattern = self._pattern(element, 10000, self.m.IUIAutomationInvokePattern)
                else:
                    pattern = self._pattern(element, 10015, self.m.IUIAutomationTogglePattern)
            except UnsupportedPattern:
                return Receipt("not_sent", "unsupported_action_pattern")
            final = self._guard(action, element=element)
            if not final.ok or final.token != token:
                return Receipt("not_sent", "context_changed_before_pattern_call")
            try:
                pattern.Invoke() if action.kind == "invoke" else pattern.Toggle()
                return Receipt("sent", "pattern_call_returned;application_result_unverified")
            except Exception as exc:
                return Receipt("uncertain", type(exc).__name__ + ":" + str(exc))
        events = self.api.click_events(normalized_point(self._rect(element), *action.point)) if action.kind == "click" else self.api.text_events(action.text)
        final = self._guard(action, element=element)
        if not final.ok or final.token != token:
            return Receipt("not_sent", "context_changed_before_sendinput")
        accepted, requested, cleanup = self.api.send(events)
        status = "sent" if accepted == requested else "not_sent" if accepted == 0 else "uncertain"
        return Receipt(status, f"SendInput;release_cleanup_accepted={cleanup}", accepted, requested)

    def list_windows(self, timeout=2):
        return [{"window": asdict(identity), "title": title} for identity, title in self.api.windows()]

    def inspect(self, hwnd, limit=100, timeout=2):
        identity = self.api.identity(hwnd)
        root = self.uia.ElementFromHandle(hwnd)
        items = root.FindAll(7, self.uia.CreateTrueCondition())
        output = []
        for i in range(min(items.Length, limit)):
            element = items.GetElement(i)
            item = {"automation_id": element.CurrentAutomationId, "name": element.CurrentName,
                    "control_type": element.CurrentControlType, "has_keyboard_focus": bool(element.CurrentHasKeyboardFocus),
                    "enabled": bool(element.CurrentIsEnabled), "offscreen": bool(element.CurrentIsOffscreen), "password": bool(element.CurrentIsPassword)}
            if not element.CurrentIsPassword:
                for pattern_id, interface, key, prop in (
                    (10002, self.m.IUIAutomationValuePattern, "value", "CurrentValue"),
                    (10015, self.m.IUIAutomationTogglePattern, "toggle", "CurrentToggleState"),
                ):
                    try:
                        item[key] = getattr(self._pattern(element, pattern_id, interface), prop)
                    except UnsupportedPattern:
                        pass
            output.append(item)
        return {"window": asdict(identity), "elements": output, "total": items.Length}

    def probe(self, timeout=2):
        # Read-only smoke test without exposing app titles or inspecting content.
        root = self.uia.GetRootElement()
        return {"dpi_mode": "PerMonitorV2", "uia_apartment": "MTA", "desktop": asdict(self.api.desktop()),
                "physical_cursor": self.api.cursor(), "uia_root_type": int(root.CurrentControlType),
                "session": self.api.interactive_state()}

    def capture_window(self, window, path, timeout=5):
        from PIL import ImageGrab
        hwnd = window['hwnd']
        session = self.api.interactive_state()
        if not session['interactive']: return {'outcome': 'failed', 'reason': session['reason']}
        if asdict(self.api.identity(hwnd)) != window or self.api.foreground() != hwnd:
            return {'outcome': 'failed', 'reason': 'target_must_be_same_foreground_window'}
        rect = self.api.rect(hwnd)
        picture = ImageGrab.grab(bbox=rect.bbox, all_screens=True)
        if self.api.foreground() != hwnd or asdict(self.api.identity(hwnd)) != window or self.api.rect(hwnd) != rect:
            return {'outcome': 'failed', 'reason': 'window_changed_during_capture'}
        if picture.size != (rect.width, rect.height): raise ValueError('capture_not_in_physical_pixels')
        picture.save(path)
        return {'outcome': 'success', 'window': window, 'physical_rect': asdict(rect), 'screenshot': path}
