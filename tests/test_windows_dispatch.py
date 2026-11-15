"""Dispatch must retain fresh checks when reusing a resolved UIA target."""
from dataclasses import replace
from types import SimpleNamespace
import unittest
from guarded_agent.model import Action, Condition, Rect, Selector, WindowIdentity
from guarded_agent.windows import WindowsBackend


class BoundTargetTests(unittest.TestCase):
    def setUp(self):
        self.window = WindowIdentity(42, 43, 44)
        self.action = Action('bound', self.window, Selector(name='Button'), 'click',
                             Condition(Selector(name='Result'), 'value', '1'))
        self.element = SimpleNamespace(CurrentIsEnabled=True, CurrentIsOffscreen=False,
            CurrentBoundingRectangle=SimpleNamespace(left=10, top=20, right=90, bottom=60),
            GetRuntimeId=lambda: (5, 6), CurrentHasKeyboardFocus=True)
        self.backend = WindowsBackend.__new__(WindowsBackend)
        self.backend.input_lease = object()
        self.selected, self.sends, self.hits = [], [], []
        self.foreground = 42
        self.locked = False
        self.backend.api = SimpleNamespace(
            identity=lambda hwnd: self.window,
            interactive_state=lambda: {'interactive': not self.locked, 'reason': 'session_locked' if self.locked else ''},
            foreground=lambda: self.foreground, rect=lambda hwnd: Rect(0, 0, 200, 100),
            modifiers_down=lambda: False,
            u=SimpleNamespace(GetDpiForWindow=lambda hwnd: 144,
                              WindowFromPoint=lambda p: 7, GetAncestor=lambda hwnd, flag: 42),
            click_events=lambda point: ['move', 'down', 'up'],
            text_events=lambda text: ['unicode-down', 'unicode-up'],
            send=lambda events: self.sends.append(events) or (len(events), len(events), None))
        self.backend.uia = SimpleNamespace(
            ElementFromPoint=lambda p: self.hits.append((p.x, p.y)) or self.element,
            CompareElements=lambda a, b: a is b, GetFocusedElement=lambda: self.element)
        self.backend._select = lambda *args, **kwargs: self.selected.append(self.element) or self.element
        self.token = self.backend.guard(self.action).token
        self.selected.clear()
        self.hits.clear()

    def dispatch(self):
        return self.backend.dispatch(self.action, self.token)

    def test_click_rechecks_hit_test_twice_but_resolves_only_once(self):
        self.assertEqual(self.dispatch().status, 'sent')
        self.assertEqual(len(self.selected), 1)
        self.assertEqual(len(self.hits), 2)
        self.assertEqual(len(self.sends), 1)

    def test_locked_dispatch_does_not_resolve_uia_target(self):
        self.locked = True
        self.assertEqual(self.dispatch().status, 'not_sent')
        self.assertEqual(self.selected, [])
        self.assertEqual(self.sends, [])

    def test_foreground_loss_during_event_build_sends_nothing(self):
        def build(point):
            self.foreground = 99
            return ['move', 'down', 'up']
        self.backend.api.click_events = build
        self.assertEqual(self.dispatch().status, 'not_sent')
        self.assertEqual(self.sends, [])

    def test_geometry_change_during_event_build_sends_nothing(self):
        def build(point):
            self.element.CurrentBoundingRectangle.left += 10
            return ['move', 'down', 'up']
        self.backend.api.click_events = build
        self.assertEqual(self.dispatch().status, 'not_sent')
        self.assertEqual(self.sends, [])

    def test_lock_during_event_build_sends_nothing(self):
        def build(point):
            self.locked = True
            return ['move', 'down', 'up']
        self.backend.api.click_events = build
        self.assertEqual(self.dispatch().status, 'not_sent')
        self.assertEqual(self.sends, [])

    def test_bound_element_disabled_during_event_build_sends_nothing(self):
        def build(point):
            self.element.CurrentIsEnabled = False
            return ['move', 'down', 'up']
        self.backend.api.click_events = build
        self.assertEqual(self.dispatch().status, 'not_sent')
        self.assertEqual(self.sends, [])

    def test_keyboard_focus_is_rechecked_after_events_built(self):
        self.action = replace(self.action, kind='type_text', text='literal')
        self.token = self.backend.guard(self.action).token
        def build(text):
            self.backend.uia.GetFocusedElement = lambda: object()
            return ['unicode-down', 'unicode-up']
        self.backend.api.text_events = build
        self.assertEqual(self.dispatch().status, 'not_sent')
        self.assertEqual(self.sends, [])

    def test_pattern_resolution_cannot_skip_final_guard(self):
        self.action = replace(self.action, kind='invoke')
        self.token = self.backend.guard(self.action).token
        self.backend.m = SimpleNamespace(IUIAutomationInvokePattern=object())
        invoked = []
        def resolve(*args):
            self.foreground = 99
            return SimpleNamespace(Invoke=lambda: invoked.append(True))
        self.backend._pattern = resolve
        self.assertEqual(self.dispatch().status, 'not_sent')
        self.assertEqual(invoked, [])
