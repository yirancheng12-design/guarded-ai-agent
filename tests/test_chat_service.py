from dataclasses import asdict
from contextlib import nullcontext
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
import json
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from guarded_agent.journal import Journal
from guarded_agent.chat_service import Controller, Store, BusyError, chat_stream
from guarded_agent.ollama_agent import OllamaClient, CalculatorSimulation, calculator_profile
from tests.test_ollama_agent import FakePlanner


def profile_data():
    window, catalog, probe = calculator_profile()
    return {'window': asdict(window), 'goal': asdict(probe.condition),
            'actions': {k: a.to_dict() for k, a in catalog.items()}}


class ChatStreamTests(unittest.TestCase):
    def client(self, chunks):
        client = OllamaClient('test')
        raw = b''.join(json.dumps(c).encode() + b'\n' for c in chunks)
        client.opener = SimpleNamespace(open=lambda *a, **k: BytesIO(raw))
        return client

    def test_stream_accumulates_text(self):
        updates = []
        result = chat_stream(self.client([{'message': {'content': '你'}}, {'message': {'content': '好'}, 'done': True}]),
                             [{'role': 'user', 'content': 'hi'}], lambda: False, updates.append)
        self.assertEqual(result, {'outcome': 'success', 'text': '你好'})
        self.assertEqual(updates, ['你', '你好'])

    def test_unfinished_stream_is_not_claimed_complete(self):
        with self.assertRaisesRegex(RuntimeError, '中断'):
            chat_stream(self.client([{'message': {'content': 'partial'}}]), [], lambda: False, lambda _: None)

    def test_chat_never_executes_tools(self):
        with self.assertRaisesRegex(ValueError, '不执行工具'):
            chat_stream(self.client([{'message': {'tool_calls': [{'function': {'name': 'click'}}]}}]), [], lambda: False, lambda _: None)

    def test_cancel_keeps_partial_text(self):
        stopped = [False]
        result = chat_stream(self.client([{'message': {'content': 'partial'}}, {'done': True}]), [],
                             lambda: stopped[0], lambda _: stopped.__setitem__(0, True))
        self.assertEqual(result, {'outcome': 'cancelled', 'text': 'partial'})


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.histories = []
        def chat(client, history, cancel, update):
            self.histories.append(history)
            update('本地回复')
            return {'outcome': 'success', 'text': '本地回复'}
        self.controller = Controller(self.temp.name, client_factory=lambda _: FakePlanner(), chat_runner=chat)
        self.conversation = self.controller.store.create()

    def wait(self):
        deadline = time.monotonic() + 15
        while self.controller.state()['active']:
            if time.monotonic() > deadline:
                self.fail('worker did not finish')
            time.sleep(.01)
        return self.controller.store.messages(self.conversation)

    def test_chat_is_saved_and_used_for_followup(self):
        self.controller.send(self.conversation, '你好', 'test')
        self.wait()
        self.controller.send(self.conversation, '继续', 'test')
        messages = self.wait()
        self.assertEqual([m['content'] for m in self.histories[-1]], ['你好', '本地回复', '继续'])
        self.assertEqual(messages[-1]['status'], 'success')

    def test_assistant_routes_explicit_open_to_launcher(self):
        with patch('guarded_agent.chat_service.AppLauncher') as launcher:
            launcher.return_value.run.return_value = {'outcome': 'success', 'text': '已打开微信'}
            self.controller.send(self.conversation, '帮我打开微信', 'test', mode='assistant')
            messages = self.wait()
            self.assertEqual(messages[-1]['mode'], 'app')
            self.assertEqual(messages[-1]['content'], '已打开微信')
            self.assertEqual(launcher.return_value.run.call_args.args[0], 'wechat')
            self.assertEqual(self.histories, [])

    def test_plain_chat_never_launches_an_app(self):
        with patch('guarded_agent.chat_service.AppLauncher') as launcher:
            self.controller.send(self.conversation, '帮我打开微信', 'test', mode='chat')
            self.wait()
            launcher.assert_not_called()

    def test_assistant_approval_is_bound_to_current_job_and_exact_request(self):
        def fake_run(client, text, history, toolbox, cancel, event, update):
            allowed = toolbox.approve('test command', {'command': 'Write-Output 1'})
            return {'outcome': 'completed' if allowed else 'cancelled', 'text': 'done'}
        with patch('guarded_agent.work_agent.run_work', side_effect=fake_run):
            job = self.controller.send(self.conversation, 'do work', 'test', mode='assistant')
            deadline = time.monotonic() + 3
            while not self.controller.state()['active'].get('approval'):
                if time.monotonic() > deadline: self.fail('approval was not shown')
                time.sleep(.01)
            approval = self.controller.state()['active']['approval']['id']
            with self.assertRaises(ValueError): self.controller.answer_approval(job, 'wrong', True)
            self.controller.answer_approval(job, approval, True)
            self.assertEqual(self.wait()[-1]['status'], 'completed')

    def test_stop_unblocks_pending_user_review_without_execution(self):
        def fake_run(client, text, history, toolbox, cancel, event, update):
            allowed = toolbox.approve('test command', {})
            return {'outcome': 'completed' if allowed else 'cancelled', 'text': 'done'}
        with patch('guarded_agent.work_agent.run_work', side_effect=fake_run):
            job = self.controller.send(self.conversation, 'do work', 'test', mode='assistant')
            self.controller.cancel(job)
            self.assertEqual(self.wait()[-1]['status'], 'cancelled')

    def test_demo_rejects_app_requests_without_creating_a_job(self):
        with self.assertRaisesRegex(ValueError, '助手模式'):
            self.controller.send(self.conversation, '打开wegame', 'test', mode='demo')
        self.assertEqual(self.controller.store.messages(self.conversation), [])

    def test_conversations_do_not_share_history(self):
        other = self.controller.store.create()
        self.controller.send(other, '另一个对话', 'test'); self.wait()
        self.controller.send(self.conversation, '当前对话', 'test'); self.wait()
        self.assertEqual([m['content'] for m in self.histories[-1]], ['当前对话'])

    def test_busy_request_does_not_create_extra_messages(self):
        gate = threading.Event()
        self.addCleanup(gate.set)
        def blocked_chat(*args):
            gate.wait(3)
            return {'outcome': 'success', 'text': 'done'}
        self.controller.chat_runner = blocked_chat
        self.controller.send(self.conversation, 'first', 'test')
        with self.assertRaises(BusyError):
            self.controller.send(self.conversation, 'second', 'test')
        self.assertEqual(len(self.controller.store.messages(self.conversation)), 2)
        gate.set()
        self.assertEqual(self.wait()[-1]['status'], 'success')

    def test_demo_uses_real_executor_and_returns_verified_steps(self):
        self.controller.send(self.conversation, 'enter 12', 'test', mode='demo')
        result = self.wait()[-1]['detail']['result']
        self.assertEqual(result['outcome'], 'success')
        self.assertEqual(len(result['steps']), 2)
        self.assertFalse((Path(self.temp.name) / 'actions.sqlite3').exists())

    def test_desktop_requires_explicit_profile(self):
        with self.assertRaisesRegex(ValueError, '导入'):
            self.controller.send(self.conversation, 'click', 'test', mode='desktop')
        self.assertEqual(self.controller.store.messages(self.conversation), [])

    def test_zero_identity_template_cannot_run(self):
        data = profile_data(); data['window']['hwnd'] = 0
        identifier = self.controller.import_profile('template', data)
        with self.assertRaisesRegex(ValueError, '有效窗口'):
            self.controller.send(self.conversation, 'click', 'test', mode='desktop', profile_id=identifier)

    def test_malformed_profile_is_rejected_before_storage(self):
        data = profile_data(); data['actions'] = ['invalid']
        with self.assertRaisesRegex(ValueError, 'actions'):
            self.controller.import_profile('bad', data)
        self.assertEqual(self.controller.store.profiles(), [])

    def test_desktop_bridge_uses_executor_and_persists_lock_across_restart(self):
        identifier = self.controller.import_profile('test', profile_data())
        backend = CalculatorSimulation(time.monotonic)
        with patch('guarded_agent.isolation.ProcessBackend', side_effect=lambda **kw: nullcontext(backend)):
            self.controller.send(self.conversation, 'enter 12', 'test', mode='desktop', profile_id=identifier)
            result = self.wait()[-1]['detail']['result']
            self.assertEqual(result['outcome'], 'success')
            self.assertEqual(backend.dispatch_count, 2)
            self.assertEqual(backend.display, '12')

            journal = Journal(Path(self.temp.name) / 'actions.sqlite3')
            action = calculator_profile()[1]['press_1']
            journal.begin(action); journal.reserve(action); journal.close()
            # A new service, conversation and request ID must reuse the same lock.
            self.controller = Controller(self.temp.name, client_factory=lambda _: FakePlanner())
            self.conversation = self.controller.store.create()
            self.controller.send(self.conversation, 'enter 12', 'test', mode='desktop', profile_id=identifier)
            result = self.wait()[-1]['detail']['result']
            self.assertEqual(result['outcome'], 'unknown')
            self.assertIn('unresolved_effect', result['reason'])
            self.assertEqual(backend.dispatch_count, 2)

    def test_desktop_cancel_during_countdown_creates_no_action_journal(self):
        identifier = self.controller.import_profile('test', profile_data())
        job = self.controller.send(self.conversation, 'click', 'test', mode='desktop', profile_id=identifier)
        self.controller.cancel(job)
        self.assertEqual(self.wait()[-1]['status'], 'cancelled')
        self.assertFalse((Path(self.temp.name) / 'actions.sqlite3').exists())

    def test_restart_marks_pending_message_interrupted(self):
        self.controller.store.begin(self.conversation, 'test', 'desktop', 'pending')
        recovered = Store(self.temp.name)
        self.assertEqual(recovered.messages(self.conversation)[-1]['status'], 'interrupted')

    def test_unknown_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            self.controller.send(self.conversation, 'test', 'test', mode='shell')
