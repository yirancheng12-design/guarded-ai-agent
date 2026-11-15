from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch, Mock
import tempfile
import time
import unittest

from guarded_agent.desktop_native import NativeDesktop, validate_input
from guarded_agent.model import Rect
from guarded_agent.work_tools import WorkTools, TOOLS
from guarded_agent.app_launcher import open_request
from guarded_agent.app_catalog import executable_entry, register_executable


class DesktopInputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.tools = WorkTools(self.root, self.root / 'data', 'test', lambda *args: True)
        self.window = {'hwnd': 7, 'pid': 8, 'created': 9}
        self.tools.windows['7'] = {'window': self.window, 'title': 'test application'}
        self.tools.desktop_snapshots = {'fresh': {'window': self.window, 'captured_at': time.monotonic(),
            'path': 'before.png', 'physical_rect': {'left': 0, 'top': 0, 'width': 500, 'height': 300}}}

    def action(self, **kwargs):
        return self.tools.desktop_action(7, 'fresh', 'click', x=10, y=20, **kwargs)

    def test_unobserved_window_cannot_receive_input(self):
        with self.assertRaises(ValueError):
            self.tools.desktop_action(99, 'fresh', 'click')

    def test_expired_snapshot_sends_nothing(self):
        self.tools.desktop_snapshots['fresh']['captured_at'] -= 121
        with patch('guarded_agent.desktop_tools.ProcessBackend') as backend:
            with self.assertRaises(ValueError): self.action()
            backend.assert_not_called()

    def test_cancel_before_reservation_sends_nothing(self):
        self.tools.cancelled = lambda: True
        with patch('guarded_agent.desktop_tools.ProcessBackend') as backend:
            with self.assertRaises(RuntimeError): self.action()
            backend.assert_not_called()

    def test_unknown_input_blocks_new_snapshot_across_tool_instances(self):
        backend = Mock()
        backend.input.side_effect = TimeoutError('worker hung after dispatch')
        with patch('guarded_agent.desktop_tools.ProcessBackend', return_value=nullcontext(backend)):
            self.assertEqual(self.action()['outcome'], 'unknown')
        other = WorkTools(self.root, self.root / 'data', 'restart', lambda *args: True)
        other.windows = self.tools.windows
        other.desktop_snapshots = {'new': dict(self.tools.desktop_snapshots['fresh'])}
        with patch('guarded_agent.desktop_tools.ProcessBackend') as backend:
            self.assertEqual(other.desktop_action(7, 'new', 'click')['outcome'], 'unknown')
            backend.assert_not_called()

    def test_sent_input_requires_new_visual_inspection(self):
        backend = Mock()
        backend.input.return_value = {'outcome': 'success', 'execution_status': 'sent', 'verified': False}
        with patch('guarded_agent.desktop_tools.ProcessBackend', return_value=nullcontext(backend)):
            result = self.action()
        self.assertFalse(result['verified'])
        self.assertIn('7', self.tools.desktop_pending)
        with self.assertRaises(ValueError): self.action()

    def test_snapshot_cannot_be_replayed_even_if_input_was_not_sent(self):
        backend = Mock()
        backend.input.return_value = {'outcome': 'failed', 'execution_status': 'not_sent'}
        with patch('guarded_agent.desktop_tools.ProcessBackend', return_value=nullcontext(backend)):
            self.action()
            with self.assertRaises(ValueError): self.action()
        self.assertEqual(backend.input.call_count, 1)

    def test_missing_vision_does_not_clear_pending_verification(self):
        backend = Mock()
        backend.snapshot.return_value = {'window': self.window, 'width': 500, 'height': 300, 'outcome': 'success'}
        self.tools.desktop_pending = {'7'}
        self.tools.describe_image = Mock(side_effect=ValueError('no vision model'))
        with patch('guarded_agent.desktop_tools.ProcessBackend', return_value=nullcontext(backend)):
            result = self.tools.desktop_snapshot(7)
        self.assertIn('analysis_error', result)
        self.assertIn('7', self.tools.desktop_pending)

    def test_keyboard_coordinates_and_wheel_validation(self):
        for arguments in [dict(kind='press', keys=['ALT', 'F4']), dict(kind='type_text', text='中文'),
                          dict(kind='scroll', amount=-3), dict(kind='drag', x=1, y=2, end_x=5, end_y=6)]:
            validate_input(**arguments)
        for arguments in [dict(kind='press', keys=['NOT_A_KEY']), dict(kind='press', keys=['CTRL', 'ctrl']),
                          dict(kind='click', x=-1), dict(kind='click', x=True), dict(kind='scroll', amount=0),
                          dict(kind='type_text', text=''), dict(kind='shell')]:
            with self.assertRaises(ValueError): validate_input(**arguments)

    def test_catalog_exposes_generic_ids_and_input_tools(self):
        specs = {tool['function']['name']: tool['function'] for tool in TOOLS}
        self.assertNotIn('enum', specs['open_app']['parameters']['properties']['app_id'])
        self.assertTrue({'register_app', 'desktop_snapshot', 'desktop_action'} <= specs.keys())

    def test_dynamic_exact_open_uses_catalog_and_compound_request_does_not(self):
        with patch('guarded_agent.app_launcher.installed_apps', return_value={'exe:word': {'name': 'Word'}}):
            self.assertEqual(open_request('打开Word'), 'exe:word')
            self.assertEqual(open_request('打开Word并删除文件'), 'unsupported')

    def test_portable_registration_persists_only_existing_exe(self):
        file = self.root / 'Portable.exe'
        file.write_bytes(b'test fixture')
        with patch('guarded_agent.app_catalog.REGISTRATIONS', self.root / 'apps.json'):
            registered = register_executable(file, '便携程序')
            self.assertTrue(registered['id'].startswith('exe:'))
            self.assertIn('便携程序', (self.root / 'apps.json').read_text(encoding='utf-8'))
            with self.assertRaises(ValueError): register_executable(self.root / 'missing.exe', 'missing')
        self.assertIsNone(executable_entry(self.root / 'data.txt'))

    def native(self):
        from PIL import Image
        native = NativeDesktop.__new__(NativeDesktop)
        native.api = Mock()
        native.api.modifiers_down.return_value = False
        native.api.cursor.return_value = (-100, -100)
        native.context = Mock(return_value=Rect(0, 0, 20, 20))
        native.snapshot = Mock(return_value={'outcome': 'success'})
        self.before = self.root / 'before.png'
        Image.new('RGB', (20, 20), 'white').save(self.before)
        self.observation = {'window': self.window, 'physical_rect': {'left': 0, 'top': 0, 'width': 20, 'height': 20}}
        return native

    def test_changed_screenshot_prevents_native_input(self):
        from PIL import Image
        native = self.native()
        with patch('PIL.ImageGrab.grab', return_value=Image.new('RGB', (20, 20), 'black')):
            result = native.input(self.observation, {'kind': 'type_text', 'text': 'test'}, str(self.before), 'unused')
        self.assertEqual(result['execution_status'], 'not_sent')
        native.api.send.assert_not_called()

    def test_partial_native_input_is_unknown_and_never_retried(self):
        from PIL import Image
        native = self.native()
        native.api.text_events.return_value = [1, 2, 3, 4]
        native.api.send.return_value = (1, 4, 1)
        with patch('PIL.ImageGrab.grab', return_value=Image.new('RGB', (20, 20), 'white')):
            result = native.input(self.observation, {'kind': 'type_text', 'text': 'test'}, str(self.before), 'unused')
        self.assertEqual(result['outcome'], 'unknown')
        native.api.send.assert_called_once()
        native.snapshot.assert_not_called()

    def test_geometry_change_prevents_native_input(self):
        native = self.native()
        native.context.return_value = Rect(0, 0, 30, 20)
        result = native.input(self.observation, {'kind': 'press', 'keys': ['ENTER']}, str(self.before), 'unused')
        self.assertEqual(result['execution_status'], 'not_sent')
        native.api.send.assert_not_called()

    def test_window_losing_foreground_before_send_prevents_input(self):
        from PIL import Image
        native = self.native()
        native.context.side_effect = [Rect(0, 0, 20, 20), RuntimeError('foreground_lost')]
        with patch('PIL.ImageGrab.grab', return_value=Image.new('RGB', (20, 20), 'white')):
            with self.assertRaises(RuntimeError):
                native.input(self.observation, {'kind': 'press', 'keys': ['ENTER']}, str(self.before), 'unused')
        native.api.send.assert_not_called()

    def test_completion_cannot_ignore_unread_desktop_result(self):
        from guarded_agent.work_agent import run_work
        self.tools.desktop_pending = {'7'}
        client = Mock()
        client.request.return_value = {'message': {'content': '已完成'}}
        result = run_work(client, '点击测试按钮', [], self.tools, lambda: False, lambda *a: None, lambda *a: None)
        self.assertEqual(result['outcome'], 'incomplete')
        self.assertIn('desktop_snapshot', result['text'])

    def test_compound_application_task_routes_to_work_agent(self):
        from guarded_agent.chat_service import Controller
        controller = Controller(self.root / 'controller')
        conversation = controller.store.create()
        with patch('guarded_agent.chat_service.threading.Thread') as thread:
            controller.send(conversation, '打开Excel并填写测试表格', 'fixture', mode='assistant')
            self.assertEqual(thread.call_args.kwargs['args'][4], 'work')

    def test_focus_fallback_detaches_input_queues_after_success(self):
        native = self.native()
        native.api.activate.side_effect = RuntimeError('foreground_activation_denied')
        native.api.k.GetCurrentThreadId.return_value = 1
        native.api.u.GetWindowThreadProcessId.side_effect = [2, 3]
        native.api.foreground.side_effect = [99, 7]
        native.api.interactive_state.return_value = {'interactive': True}
        native.api.u.AttachThreadInput.return_value = True
        native.activate(7)
        calls = native.api.u.AttachThreadInput.call_args_list
        self.assertEqual(sum(call.args[-1] is True for call in calls), 2)
        self.assertEqual(sum(call.args[-1] is False for call in calls), 2)

    def test_focus_fallback_detaches_even_when_windows_rejects_focus(self):
        native = self.native()
        native.api.activate.side_effect = RuntimeError('foreground_activation_denied')
        native.api.k.GetCurrentThreadId.return_value = 1
        native.api.u.GetWindowThreadProcessId.side_effect = [2, 3]
        native.api.foreground.return_value = 99
        native.api.interactive_state.return_value = {'interactive': True}
        native.api.u.AttachThreadInput.return_value = True
        native.api.u.SetForegroundWindow.side_effect = RuntimeError('denied')
        with self.assertRaises(RuntimeError): native.activate(7)
        calls = native.api.u.AttachThreadInput.call_args_list
        self.assertEqual(sum(call.args[-1] is False for call in calls), 2)
