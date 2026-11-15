from pathlib import Path
from contextlib import nullcontext
from copy import deepcopy
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import patch
import json
import os
import tempfile
import unittest
from guarded_agent.work_tools import WorkTools, Declined, public_get, PageText
from guarded_agent.work_agent import run_work
from guarded_agent.ollama_agent import calculator_profile


class Client:
    model = 'test'
    def __init__(self, messages): self.messages = iter(messages); self.calls = 0
    def request(self, *args, **kwargs):
        self.calls += 1
        return {'message': next(self.messages)}


def call(name, **arguments):
    return {'role': 'assistant', 'tool_calls': [{'function': {'name': name, 'arguments': arguments}}]}


class WorkToolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name) / 'files'; self.folder.mkdir()
        self.data = Path(self.temp.name) / 'data'
        self.tools = WorkTools(self.folder, self.data, 'run1', lambda *a: False)

    def test_path_escape_is_rejected(self):
        for path in ('../secret.txt', 'C:\\secret.txt', '//host/share/a', 'a.txt:stream'):
            with self.subTest(path=path), self.assertRaises(ValueError): self.tools.path(path)

    def test_credential_paths_are_rejected(self):
        for path in ('.env', '.ssh/id_rsa', 'secret.pem'):
            with self.subTest(path=path), self.assertRaises(ValueError): self.tools.path(path)

    def test_file_write_is_verified_and_backup_is_recoverable(self):
        first = self.tools.write_file('notes/计划.txt', '上午学习\n下午休息', '')
        read = self.tools.read_file('notes/计划.txt')
        self.assertEqual(first['sha256'], read['sha256'])
        second = self.tools.write_file('notes/计划.txt', '改后的计划', read['sha256'])
        self.assertEqual(Path(second['backup']).read_text(encoding='utf-8'), '上午学习\n下午休息')
        self.assertEqual(self.tools.read_file('notes/计划.txt')['text'], '改后的计划')

    def test_stale_file_hash_never_overwrites(self):
        self.tools.write_file('test.txt', 'original', '')
        with self.assertRaises(ValueError): self.tools.write_file('test.txt', 'new', '')
        self.assertEqual((self.folder / 'test.txt').read_text(), 'original')

    def test_move_never_overwrites_existing_destination(self):
        self.tools.write_file('a.txt', 'a', ''); self.tools.write_file('b.txt', 'b', '')
        with self.assertRaises(ValueError): self.tools.move_file('a.txt', 'b.txt')
        self.assertEqual((self.folder / 'b.txt').read_text(), 'b')

    def test_command_declined_sends_nothing(self):
        with patch.object(self.tools, '_run_approved_command') as execute:
            with self.assertRaises(Declined): self.tools.run_command('Write-Output test')
            execute.assert_not_called()

    def test_unknown_command_lock_survives_new_tool_instance(self):
        self.tools.approve = lambda *a: True
        with patch.object(self.tools, '_run_approved_command', return_value={'outcome': 'unknown'}) as execute:
            self.assertEqual(self.tools.run_command('test')['outcome'], 'unknown')
            execute.assert_called_once()
        again = WorkTools(self.folder, self.data, 'run2', lambda *a: True)
        with patch.object(again, '_run_approved_command') as execute:
            self.assertEqual(again.run_command('test')['reason'], 'unresolved_command_no_retry')
            execute.assert_not_called()

    def test_uninspected_window_cannot_receive_input(self):
        with self.assertRaisesRegex(ValueError, '先读取'):
            self.tools.ui_action(123, '{}')

    def test_tool_extra_arguments_are_rejected(self):
        with self.assertRaises(ValueError): self.tools.execute('list_files', {'path': '.', 'shell': 'oops'})

    @unittest.skipUnless(os.name == 'nt', 'Windows command integration')
    def test_reviewed_command_returns_real_exit_code_and_output(self):
        self.tools.approve = lambda *a: True
        result = self.tools.run_command("Write-Output 'Guarded integration OK'")
        self.assertEqual(result['outcome'], 'success')
        self.assertEqual(result['exit_code'], 0)
        self.assertIn('Guarded integration OK', result['output'])

    @unittest.skipUnless(os.name == 'nt', 'Windows process cancellation integration')
    def test_stop_terminates_owned_command_and_blocks_replay(self):
        self.tools.approve = lambda *a: True
        calls = [0]
        def cancelled():
            calls[0] += 1
            return calls[0] > 3
        self.tools.cancelled = cancelled
        result = self.tools.run_command('Start-Sleep -Seconds 30')
        self.assertEqual(result['outcome'], 'unknown')
        self.tools.cancelled = lambda: False
        self.assertEqual(self.tools.run_command('Start-Sleep -Seconds 30')['reason'], 'unresolved_command_no_retry')

    def test_changed_ui_is_rejected_before_requesting_input_permission(self):
        window, catalog, _ = calculator_profile()
        observed = {'window': asdict(window), 'elements': [{'name': '1', 'enabled': True}], 'total': 1}
        self.tools.windows = {'1': {'window': asdict(window), 'title': 'Fixture'}}
        self.tools.inspected = {'1': observed}
        changed = deepcopy(observed); changed['elements'][0]['enabled'] = False
        backend = SimpleNamespace(inspect=lambda *a, **k: changed)
        specification = {'kind': 'click', 'target': {'name': '1'}, 'condition': asdict(catalog['press_1'].condition)}
        with patch('guarded_agent.work_tools.ProcessBackend', return_value=nullcontext(backend)), patch.object(self.tools, 'approve') as approve:
            result = self.tools.ui_action(1, json.dumps(specification))
        self.assertEqual(result['execution_status'], 'not_sent')
        self.assertEqual(result['reason'], 'ui_changed_while_planning')
        approve.assert_not_called()

    def test_private_web_addresses_are_rejected_before_connecting(self):
        with patch('socket.getaddrinfo', return_value=[(2, 1, 6, '', ('127.0.0.1', 80))]), patch('socket.create_connection') as connect:
            with self.assertRaisesRegex(ValueError, '私有地址'): public_get('http://example.com')
            connect.assert_not_called()

    def test_structured_desktop_action_reaches_review_without_sending(self):
        window, catalog, _ = calculator_profile()
        observed = {'window': asdict(window), 'elements': [{'name': '1', 'enabled': True}], 'total': 1}
        self.tools.windows = {'1': {'window': asdict(window), 'title': 'Fixture'}}
        self.tools.inspected = {'1': observed}
        backend = SimpleNamespace(inspect=lambda *a, **k: observed)
        specification = {'kind': 'click', 'target': {'name': '1'}, 'condition': asdict(catalog['press_1'].condition)}
        with patch('guarded_agent.work_tools.ProcessBackend', return_value=nullcontext(backend)), patch('guarded_agent.work_tools.Executor') as executor:
            with self.assertRaises(Declined): self.tools.ui_action(1, specification)
            executor.assert_not_called()

    def test_malformed_tool_arguments_preserve_prior_saved_file(self):
        malformed = {'role': 'assistant', 'tool_calls': [{'function': {'name': 'read_file', 'arguments': '{invalid'}}]}
        client = Client([call('write_file', path='saved.txt', content='saved', expected_sha256=''), malformed])
        result = run_work(client, 'test', [], self.tools, lambda: False, lambda _: None, lambda _: None)
        self.assertEqual(result['outcome'], 'unknown')
        self.assertEqual(len(result['steps']), 1)
        self.assertEqual((self.folder / 'saved.txt').read_text(), 'saved')

    def test_local_file_context_requires_review_before_network(self):
        self.tools.write_file('notes.txt', 'local data', '')
        self.tools.read_file('notes.txt')
        with patch('guarded_agent.work_tools.public_get') as get:
            with self.assertRaises(Declined): self.tools.fetch_url('https://example.com')
            get.assert_not_called()

    def test_html_scripts_are_not_returned_as_page_text(self):
        parser = PageText(); parser.feed('<p>Hello</p><script>secret()</script><p>World</p>')
        self.assertNotIn('secret', ''.join(parser.parts))
        self.assertIn('Hello', ''.join(parser.parts))

    def test_failed_tests_interleaved_with_unchanged_reads_are_not_replayed(self):
        for name in ('a.py', 'b.py', 'test.py'): (self.folder / name).write_text('unchanged')
        sequence = [call('read_file', path='a.py'), call('read_file', path='b.py'),
                    call('read_file', path='test.py'), call('run_command', command='python -m unittest')]
        client = Client(sequence * 20)
        failure = {'outcome': 'failed', 'exit_code': 1, 'output': 'AssertionError: 0 != 2'}
        with patch.object(self.tools, 'run_command', return_value=failure) as run:
            result = run_work(client, '检查状态', [], self.tools, lambda: False, lambda _: None, lambda _: None)
        run.assert_called_once()
        self.assertEqual(result['outcome'], 'incomplete')
        self.assertIn('AssertionError: 0 != 2', result['text'])
        self.assertLessEqual(client.calls, 16)
        self.assertEqual(result['model_recovery']['repetition_recoveries'], 2)

    def test_failed_test_can_run_again_after_a_real_file_edit(self):
        from hashlib import sha256
        (self.folder / 'a.py').write_text('old')
        version = sha256(b'old').hexdigest()
        client = Client([call('read_file', path='a.py'), call('run_command', command='test'),
                         call('read_file', path='a.py'),
                         call('edit_file', path='a.py', old_text='old', new_text='new', expected_sha256=version),
                         call('run_command', command='test'), {'role': 'assistant', 'content': '已修正并验证。'}])
        with patch.object(self.tools, 'run_command', side_effect=[
                {'outcome': 'failed', 'exit_code': 1, 'output': 'failure'},
                {'outcome': 'success', 'exit_code': 0, 'output': 'OK'}]) as run:
            result = run_work(client, '检查状态', [], self.tools, lambda: False, lambda _: None, lambda _: None)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(result['outcome'], 'completed')
        self.assertEqual((self.folder / 'a.py').read_text(), 'new')

    def test_alternating_unchanged_reads_stop_without_waiting_for_timeout(self):
        for name in ('a.py', 'b.py'): (self.folder / name).write_text('unchanged')
        client = Client([call('read_file', path='a.py'), call('read_file', path='b.py')] * 20)
        result = run_work(client, '检查状态', [], self.tools, lambda: False, lambda _: None, lambda _: None)
        self.assertEqual(result['outcome'], 'incomplete')
        self.assertIn('未检测到任务进展', result['text'])
        self.assertLessEqual(client.calls, 16)

    def test_continuation_checkpoint_keeps_objective_across_restarts(self):
        self.tools.task_state = {'task': '继续', 'plan': []}
        history = [{'role': 'user', 'content': '整理 Guarded 文件并上传到指定仓库'},
                   {'role': 'user', 'content': '继续'}]
        result = run_work(Client([{'role': 'assistant', 'content': '已保留记录。'}]), '继续', history,
                          self.tools, lambda: False, lambda _: None, lambda _: None)
        state = json.loads(self.tools.task_file.read_text(encoding='utf-8'))
        self.assertEqual(state['objective'], history[0]['content'])
        self.assertEqual(state['task'], '继续')
        self.assertEqual(result['outcome'], 'completed')

    def test_generation_timeout_is_resumable_and_dispatches_nothing(self):
        import time
        self.tools.task_timeout = .001
        class SlowClient:
            model = 'test'
            def request(inner, *args):
                time.sleep(.01)
                return {'message': call('write_file', path='must-not-exist', content='x', expected_sha256='')}
        result = run_work(SlowClient(), '检查状态', [], self.tools, lambda: False, lambda _: None, lambda _: None)
        self.assertEqual(result['outcome'], 'incomplete')
        self.assertEqual(result['steps'], [])
        self.assertFalse((self.folder / 'must-not-exist').exists())

    def test_progressing_task_can_cross_old_thirty_minute_limit(self):
        clock = [0]
        class LongClient:
            model = 'test'
            calls = 0
            def request(inner, *args):
                inner.calls += 1
                clock[0] += 1900
                return {'message': call('list_files', path='.') if inner.calls == 1 else {'content': '已列出目录。'}}
        with patch('guarded_agent.work_agent.time.monotonic', side_effect=lambda: clock[0]):
            result = run_work(LongClient(), '列出文件', [], self.tools, lambda: False, lambda _: None, lambda _: None)
        self.assertEqual(result['outcome'], 'completed')
        self.assertEqual(result['steps'][0]['tool'], 'list_files')

    def test_generation_timeout_recovers_without_replaying_successful_write(self):
        class RecoverClient:
            model = 'test'
            thinking = True
            calls = 0
            def request_work(inner, request, cancelled, event, timeout):
                inner.calls += 1
                if inner.calls == 1:
                    return {'message': call('write_file', path='once.txt', content='once', expected_sha256='')}
                if inner.calls == 2: raise TimeoutError('generation stalled')
                self.assertFalse(request['think'])
                self.assertLessEqual(request['options']['num_predict'], 1024)
                return {'message': {'content': '文件已保存。'}}
        result = run_work(RecoverClient(), '创建 once.txt', [], self.tools, lambda: False, lambda _: None, lambda _: None)
        self.assertEqual(result['outcome'], 'completed')
        self.assertEqual(len([s for s in result['steps'] if s['tool']=='write_file']), 1)
        self.assertEqual((self.folder/'once.txt').read_text(), 'once')

    def test_repeated_generation_timeout_stops_after_one_recovery(self):
        client = SimpleNamespace(model='test', request_work=__import__('unittest.mock', fromlist=['Mock']).Mock(side_effect=TimeoutError('stalled')))
        result = run_work(client, '检查状态', [], self.tools, lambda: False, lambda _: None, lambda _: None)
        self.assertEqual(result['outcome'], 'incomplete')
        self.assertEqual(client.request_work.call_count, 2)
        self.assertEqual(result['steps'], [])

    def test_repeated_command_is_not_dispatched_and_model_can_recover(self):
        client = Client([call('run_command', command='git branch -a'),
                         call('run_command', command='git branch -a'),
                         call('read_file', path='README.md'),
                         {'role': 'assistant', 'content': '已检查具体原因。'}])
        (self.folder / 'README.md').write_text('empty repository')
        with patch.object(self.tools, 'run_command', return_value={'outcome': 'success', 'exit_code': 0, 'output': ''}) as run:
            result = run_work(client, '检查状态', [], self.tools, lambda: False, lambda _: None, lambda _: None)
        run.assert_called_once()
        self.assertEqual(result['outcome'], 'completed')
        self.assertEqual([s['tool'] for s in result['steps']], ['run_command', 'read_file'])

    def test_persistent_repetition_stops_before_any_duplicate_dispatch(self):
        client = Client([call('run_command', command='git branch -a') for _ in range(4)])
        with patch.object(self.tools, 'run_command', return_value={'outcome': 'success', 'exit_code': 0, 'output': ''}) as run:
            result = run_work(client, '检查状态', [], self.tools, lambda: False, lambda _: None, lambda _: None)
        run.assert_called_once()
        self.assertEqual(result['outcome'], 'incomplete')
        self.assertIn('run_command', result['text'])
        self.assertEqual(len(result['steps']), 1)

    def test_resume_restores_previous_plan_and_loads_record_before_planning(self):
        previous = {'task': '整理文件', 'plan': [{'title': '整理文件', 'status': 'in_progress'}],
                    'steps': [{'tool': 'run_command', 'arguments': {'command': 'git branch -a'},
                               'result': {'output': '', 'exit_code': 0}}]}
        self.tools.task_state = previous
        client = Client([call('update_plan', steps=[{'title': '整理文件', 'status': 'completed'}]),
                         {'role': 'assistant', 'content': '已核对历史记录。'}])
        result = run_work(client, '继续未完成部分', [], self.tools, lambda: False, lambda _: None, lambda _: None)
        self.assertEqual(result['steps'][0]['tool'], 'task_status')
        self.assertEqual(result['steps'][0]['result']['previous'], previous)
        self.assertEqual(result['steps'][0]['result']['plan'], previous['plan'])
        self.assertEqual(result['outcome'], 'completed')

    def test_tool_loop_saves_real_file_before_reporting(self):
        client = Client([call('write_file', path='hello.py', content='print(42)\n', expected_sha256=''),
                         call('read_file', path='hello.py'), {'role': 'assistant', 'content': '已保存并读取确认。'}])
        result = run_work(client, 'create script', [], self.tools, lambda: False, lambda _: None, lambda _: None)
        self.assertEqual(result['outcome'], 'completed')
        self.assertEqual((self.folder / 'hello.py').read_text(), 'print(42)\n')
        self.assertEqual(len(result['steps']), 2)
        self.assertEqual(result['artifacts'][0]['path'], 'hello.py')

    def test_unknown_tool_result_stops_model_loop(self):
        client = Client([call('list_files')])
        with patch.object(self.tools, 'execute', return_value={'outcome': 'unknown'}):
            result = run_work(client, 'test', [], self.tools, lambda: False, lambda _: None, lambda _: None)
        self.assertEqual(result['outcome'], 'unknown')
        self.assertEqual(client.calls, 1)

    def test_declined_tool_is_not_rephrased_and_retried(self):
        client = Client([call('run_command', command='Get-Date')])
        result = run_work(client, 'test', [], self.tools, lambda: False, lambda _: None, lambda _: None)
        self.assertEqual(result['outcome'], 'cancelled')
        self.assertEqual(client.calls, 1)

    def test_pre_cancel_never_calls_model(self):
        client = Client([])
        self.assertEqual(run_work(client, 'test', [], self.tools, lambda: True, lambda _: None, lambda _: None)['outcome'], 'cancelled')
        self.assertEqual(client.calls, 0)

    def test_model_disconnect_preserves_completed_steps_and_files(self):
        client = Client([call('write_file', path='saved.txt', content='saved', expected_sha256='')])
        result = run_work(client, 'test', [], self.tools, lambda: False, lambda _: None, lambda _: None)
        self.assertEqual(result['outcome'], 'unknown')
        self.assertEqual(len(result['steps']), 1)
        self.assertEqual(result['artifacts'][0]['path'], 'saved.txt')
        self.assertEqual((self.folder / 'saved.txt').read_text(), 'saved')

    def test_unlimited_planning_and_search_exceed_all_old_count_limits(self):
        client = Client([call('search_web', query=f'query {number}') for number in range(100)] +
                        [{'role': 'assistant', 'content': '搜索完成'}])
        with patch.object(self.tools, 'execute', side_effect=[
                {'outcome': 'success', 'results': [{'url': f'https://example.com/source/{i}'}]}
                for i in range(100)]) as execute:
            result = run_work(client, 'test', [], self.tools, lambda: False, lambda _: None, lambda _: None)
        self.assertEqual(result['outcome'], 'completed')
        self.assertEqual(execute.call_count, 100)
        self.assertEqual(client.calls, 101)

    def test_explicit_finite_planning_budget_still_works(self):
        client = Client([call('list_files', path='first'), call('list_files', path='second')])
        with patch.object(self.tools, 'execute', return_value={'outcome': 'success'}) as execute:
            result = run_work(client, 'test', [], self.tools, lambda: False, lambda _: None, lambda _: None, max_rounds=2)
        self.assertEqual(result['outcome'], 'incomplete')
        self.assertIn('规划上限', result['text'])
        self.assertEqual(execute.call_count, 2)

    def test_unlimited_planning_still_honors_cancel(self):
        client = Client([call('search_web', query=f'query {n}') for n in range(10)])
        with patch.object(self.tools, 'execute', return_value={'outcome': 'success'}) as execute:
            result = run_work(client, 'test', [], self.tools, lambda: client.calls >= 3, lambda _: None, lambda _: None)
        self.assertEqual(result['outcome'], 'cancelled')
        self.assertEqual(execute.call_count, 2)

    def test_system_cancelled_launch_is_never_retried(self):
        client = Client([call('open_app', app_id='wegame')])
        with patch.object(self.tools, 'execute', return_value={'outcome': 'cancelled', 'text': 'Windows cancelled'}):
            result = run_work(client, 'test', [], self.tools, lambda: False, lambda _: None, lambda _: None)
        self.assertEqual(result['outcome'], 'cancelled')
        self.assertEqual(client.calls, 1)
