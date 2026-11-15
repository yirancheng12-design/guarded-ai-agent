from pathlib import Path
import json
import tempfile
import threading
import time
import unittest
from unittest.mock import patch, Mock
import uuid
from contextlib import nullcontext
from types import SimpleNamespace

from guarded_agent.simon_bridge import (Control, DesktopTools, DESKTOP_NAMES,
                                       desktop_lock, validate_start, run_desktop, CancellableClient)
from guarded_agent.simon_policy import review_reason
from guarded_agent.work_agent import run_work
from guarded_agent.work_tools import Declined


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.id = str(uuid.uuid4())
        self.events = []
        self.control = Control(self.id, self.events.append)
        self.tools = DesktopTools(self.root, self.root / 'data', self.id, lambda *a: False)
        self.tools.directory.mkdir(parents=True, exist_ok=True)

    def test_native_tools_only_and_no_raw_coordinates(self):
        names = {item['function']['name'] for item in self.tools.tool_specs}
        self.assertEqual(names, DESKTOP_NAMES)
        for name in ('run_command', 'write_file', 'browser_action', '__dict__'):
            with self.assertRaises(Declined):
                self.tools.execute(name, {})

    def test_open_requires_review_before_launch(self):
        self.tools.auto_execute = False
        with patch('guarded_agent.work_tools.AppLauncher') as launch:
            with self.assertRaises(Declined):
                self.tools.execute('open_app', {'app_id': 'notepad'})
            launch.assert_not_called()

    def test_ordinary_launch_is_automatic_and_fast_without_model(self):
        client = Mock()
        with patch('guarded_agent.work_tools.AppLauncher') as launch:
            launch.return_value.run.return_value = {'outcome': 'success', 'text': '已验证记事本。'}
            result = run_desktop(client, '帮我打开记事本', self.tools, lambda: False, self.events.append, lambda _: None)
            launch.return_value.run.assert_called_once()
        client.request.assert_not_called()
        self.assertTrue(result['fast_path'])
        self.assertEqual(result['outcome'], 'completed')
        self.assertEqual(result['text'], '已验证记事本。')

    def test_fast_unknown_never_calls_model_or_retries(self):
        with patch.object(self.tools, 'execute', return_value={'outcome': 'unknown', 'text': '窗口不可见'}) as execute:
            client = Mock()
            result = run_desktop(client, '打开微信', self.tools, lambda: False, self.events.append, lambda _: None)
            execute.assert_called_once()
            client.request.assert_not_called()
            self.assertEqual(result['outcome'], 'unknown')

    def test_fast_cancel_before_dispatch(self):
        with patch.object(self.tools, 'execute') as execute:
            result = run_desktop(Mock(), '打开微信', self.tools, lambda: True, self.events.append, lambda _: None)
            execute.assert_not_called()
            self.assertEqual(result['outcome'], 'cancelled')

    def test_compound_or_negated_request_cannot_use_fast_path(self):
        for task in ('不要打开微信', '打开微信并发送你好', '解释“打开记事本”', '打开微信，然后删除聊天'):
            with patch('guarded_agent.simon_bridge.run_work', return_value={'outcome': 'fixture'}) as planner, \
                    patch.object(self.tools, 'execute') as execute:
                run_desktop(Mock(), task, self.tools, lambda: False, self.events.append, lambda _: None)
                execute.assert_not_called()
                planner.assert_called_once()

    def test_review_uses_observed_full_element_not_model_selector(self):
        for name in ('发送', 'Send', '删除', '支付', '确认订单', '上传文件', '登录', 'Submit'):
            details = {'action': {'kind': 'invoke', 'target': {'automation_id': 'search'}},
                       'target_element': {'name': name, 'automation_id': 'search', 'control_type': 50000}}
            with self.assertRaises(Declined, msg=name):
                self.tools.review('执行桌面动作', details)

    def test_normal_input_and_navigation_do_not_ask(self):
        for action, element in [({'kind': 'type_text', 'text': '普通草稿'}, {'name': '内容', 'control_type': 50004}),
                                ({'kind': 'invoke'}, {'name': '搜索', 'control_type': 50000}),
                                ({'kind': 'click'}, {'name': '文件', 'control_type': 50019})]:
            self.tools.review('执行桌面动作', {'action': action, 'target_element': element})
            self.assertEqual(self.tools.desktop_prepare_seconds, 0)

    def test_uncertain_button_multiline_and_terminal_require_review(self):
        cases = [({'kind': 'invoke'}, {'name': '确定', 'control_type': 50000}, ''),
                 ({'kind': 'type_text', 'text': 'hello\n'}, {'name': '内容', 'control_type': 50004}, ''),
                 ({'kind': 'type_text', 'text': 'anything'}, {'name': '输入', 'control_type': 50004}, 'PowerShell')]
        for action, element, window in cases:
            with self.assertRaises(Declined):
                self.tools.review('执行桌面动作', {'action': action, 'target_element': element, 'window': window})

    def test_stop_also_blocks_automatic_review(self):
        self.tools.cancelled = lambda: True
        with self.assertRaises(Declined):
            self.tools.review('打开应用', {'app_id': 'notepad'})

    def test_auto_ui_action_keeps_fresh_guard_and_has_no_countdown(self):
        window = {'hwnd': 1, 'pid': 2, 'created': 3}
        element = {'name': '搜索', 'control_type': 50000, 'password': False}
        observed = {'window': window, 'elements': [element], 'total': 1}
        self.tools.windows = {'1': {'window': window, 'title': '测试窗口'}}
        self.tools.inspected = {'1': observed}
        spec = {'kind': 'invoke', 'target': {'name': '搜索'},
                'condition': {'selector': {'name': '结果'}, 'property': 'exists', 'expected': True}}
        backend = Mock()
        backend.inspect.return_value = observed
        with patch('guarded_agent.work_tools.ProcessBackend', side_effect=lambda **kw: nullcontext(backend)), \
                patch('guarded_agent.work_tools.time.sleep') as sleep, \
                patch('guarded_agent.work_tools.Executor') as executor:
            executor.return_value.run.return_value.to_dict.return_value = {'outcome': 'success'}
            self.assertEqual(self.tools.ui_action(1, spec)['outcome'], 'success')
            self.assertEqual(backend.inspect.call_count, 2)
            executor.return_value.run.assert_called_once()
            sleep.assert_not_called()

    def test_cancel_after_fresh_inspection_still_prevents_dispatch(self):
        window = {'hwnd': 1, 'pid': 2, 'created': 3}
        observed = {'window': window, 'elements': [{'name': '搜索', 'control_type': 50000}], 'total': 1}
        self.tools.windows = {'1': {'window': window, 'title': '测试窗口'}}
        self.tools.inspected = {'1': observed}
        def inspect(*args, **kwargs):
            if backend.inspect.call_count == 2:
                self.tools.cancelled = lambda: True
            return observed
        backend = Mock()
        backend.inspect.side_effect = inspect
        spec = {'kind': 'invoke', 'target': {'name': '搜索'},
                'condition': {'selector': {'name': '结果'}, 'property': 'exists', 'expected': True}}
        with patch('guarded_agent.work_tools.ProcessBackend', side_effect=lambda **kw: nullcontext(backend)), \
                patch('guarded_agent.work_tools.Executor') as executor:
            with self.assertRaises(Declined):
                self.tools.ui_action(1, spec)
            executor.assert_not_called()

    def test_approval_bound_to_job_and_request_and_single_use(self):
        answer = []
        thread = threading.Thread(target=lambda: answer.append(self.control.approve('test', {})))
        thread.start()
        for _ in range(100):
            if self.events:
                break
            time.sleep(.01)
        approval_id = self.events[0]['id']
        self.control.receive({'run_id': 'other', 'type': 'approval', 'approval_id': approval_id, 'allowed': True})
        self.control.receive({'run_id': self.id, 'type': 'approval', 'approval_id': 'old', 'allowed': True})
        self.assertFalse(self.control.gate.is_set())
        self.control.receive({'run_id': self.id, 'type': 'approval', 'approval_id': approval_id, 'allowed': True})
        thread.join(2)
        self.assertEqual(answer, [True])
        self.assertIsNone(self.control.pending)

    def test_cancel_blocks_queued_approval(self):
        self.control.receive({'run_id': self.id, 'type': 'cancel'})
        self.assertFalse(self.control.approve('test', {}))
        self.assertEqual(self.events, [])

    def test_expired_lease_cannot_be_revived(self):
        self.control.last_seen -= 31
        self.control.receive({'run_id': self.id, 'type': 'heartbeat'})
        self.assertTrue(self.control.cancelled())
        self.assertFalse(self.control.approve('test', {}))

    def test_desktop_lock_serializes_sessions_and_releases(self):
        with desktop_lock(self.root):
            with self.assertRaisesRegex(RuntimeError, '另一个'):
                with desktop_lock(self.root):
                    self.fail('second session acquired lock')
        with desktop_lock(self.root):
            pass

    def test_model_receives_only_restricted_schemas(self):
        tools = self.tools
        class Client:
            model = 'fixture'
            def request(inner, endpoint, request):
                self.assertEqual({t['function']['name'] for t in request['tools']}, DESKTOP_NAMES)
                self.assertIn('SimonAI', request['messages'][0]['content'])
                return {'message': {'content': '没有操作。'}}
        result = run_work(Client(), 'test', [], tools, lambda: False, lambda e: None, lambda e: None)
        self.assertEqual(result['steps'], [])

    def test_unknown_stops_before_second_model_or_action(self):
        class Client:
            model = 'fixture'
            calls = 0
            def request(inner, *args):
                inner.calls += 1
                return {'message': {'tool_calls': [{'function': {'name': 'ui_action', 'arguments': {}}}]}}
        client = Client()
        with patch.object(self.tools, 'execute', return_value={'outcome': 'unknown', 'execution_status': 'uncertain'}) as execute:
            result = run_work(client, 'test', [], self.tools, lambda: False, lambda e: None, lambda e: None)
            execute.assert_called_once()
        self.assertEqual(result['outcome'], 'unknown')
        self.assertEqual(client.calls, 1)

    def test_malicious_tool_request_stops_without_side_effect(self):
        class Client:
            model = 'fixture'
            def request(inner, *args):
                return {'message': {'tool_calls': [{'function': {'name': 'run_command', 'arguments': {'command': 'bad'}}}]}}
        with patch.object(self.tools, 'run_command') as command:
            result = run_work(Client(), 'test', [], self.tools, lambda: False, lambda e: None, lambda e: None)
            command.assert_not_called()
        self.assertEqual(result['outcome'], 'cancelled')

    def test_start_rejects_remote_model_before_work(self):
        request = {'type': 'start', 'run_id': self.id, 'conversation_id': str(uuid.uuid4()),
                   'task': 'test', 'model': 'fixture', 'base_url': 'https://example.com'}
        with self.assertRaises(ValueError):
            validate_start(request)
        request['base_url'] = 'http://127.0.0.1:11434'
        self.assertEqual(validate_start(request), request)
        request['run_id'] = '../../data'
        with self.assertRaises(ValueError):
            validate_start(request)

    def test_cancel_ends_model_wait_without_dispatch(self):
        stopped, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def blocked(*args, **kwargs):
            release.wait(3)
            return {'message': {'content': 'late'}}
        with patch('guarded_agent.simon_bridge.OllamaClient.request', side_effect=blocked):
            client = CancellableClient('fixture', cancelled=stopped.is_set)
            timer = threading.Timer(.15, stopped.set); timer.start()
            started = time.monotonic()
            with self.assertRaises(Declined): client.request('/api/chat', {})
            self.assertLess(time.monotonic() - started, 1)
            release.set(); timer.join()

    def test_full_access_setting_must_be_host_boolean(self):
        request = {'type': 'start', 'run_id': self.id, 'conversation_id': str(uuid.uuid4()),
                   'task': 'test', 'model': 'fixture', 'base_url': 'http://127.0.0.1:11434'}
        for value in ('true', 1, None, {}, []):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_start({**request, 'full_access': value})
        for value in (False, True):
            self.assertIs(validate_start({**request, 'full_access': value})['full_access'], value)


if __name__ == '__main__':
    unittest.main()
