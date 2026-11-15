"""Regressions for real source loss, range replay, and recovery across progress."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import unittest

from guarded_agent.read_progress import ReadProgress
from guarded_agent.simon_work import SimonWorkTools
from guarded_agent.work_agent import run_work
from guarded_agent.work_tools import WorkTools
from guarded_agent.planning_context import build_context, input_budget, tokens


def call(name, **args):
    return {'message': {'tool_calls': [{'function': {'name': name, 'arguments': args}}]}}


class ReadRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'workspace'
        self.root.mkdir()
        self.data = Path(self.temp.name) / 'data'
        self.tools = WorkTools(self.root, self.data, 'recovery', lambda *a: False)

    def test_full_coverage_replays_requested_range_instead_of_eof_tail(self):
        source = 'A'*1800 + 'EDIT_THIS_MIDDLE' + 'B'*5200
        (self.root / 'a.js').write_text(source)
        reads = ReadProgress(self.tools)
        args = {'path': 'a.js'}
        while args:
            result = self.tools.execute('read_file', args)
            reads.observe('read_file', args, result)
            args = result['next_read']
        replay = reads.repeated('read_file', {'path': './a.js'})
        self.assertEqual(replay['text'], source[:4000])
        self.assertEqual(replay['char_offset'], 0)
        self.assertEqual(replay['returned_chars'], 4000)
        self.assertIsNone(replay['next_read'])
        self.assertEqual(reads.repeated('read_file', {'path': 'a.js', 'char_offset': 4000})['text'], source[4000:])

    def test_legacy_shortened_checkpoint_is_not_counted_as_delivered_source(self):
        (self.root / 'a.py').write_text('A'*4000)
        args = {'path': 'a.py'}
        old = self.tools.execute('read_file', args)
        old['text'] = old['text'][:800] + '\n[省略中间内容]\n' + old['text'][-1200:]
        reads = ReadProgress(self.tools)
        reads.observe('read_file', args, old)
        self.assertIsNone(reads.repeated('read_file', args))

    def test_checkpoint_preserves_exact_read_metadata_and_batch(self):
        tools = SimonWorkTools(self.root, self.data, 'batch', lambda *a: False)
        self.addCleanup(tools.close)
        (self.root / 'a.py').write_text('x'*7000)
        args = {'paths': ['a.py']}
        result = tools.execute('read_files', args)
        steps = [{'tool': 'read_files', 'arguments': args, 'result': result}]
        tools.checkpoint('检查状态', steps)
        self.assertEqual(json.loads(tools.task_file.read_text(encoding='utf-8'))['steps'], steps)

    def test_restart_does_not_replay_expired_short_edit_tokens(self):
        tools = SimonWorkTools(self.root, self.data, 'first', lambda *a: False)
        self.addCleanup(tools.close)
        (self.root / 'a.py').write_text('old')
        args = {'path': 'a.py'}
        result = tools.execute('read_file', args)
        resumed = SimonWorkTools(self.root, self.data, 'second', lambda *a: False)
        self.addCleanup(resumed.close)
        reads = ReadProgress(resumed)
        reads.observe('read_file', args, result)
        self.assertIsNone(reads.repeated('read_file', args))

    def test_forced_context_preserves_source_middle_when_it_fits(self):
        task = '修改源码'
        source = 'A'*1900 + 'EXACT_EDIT_TARGET' + 'B'*1900
        read = {'text': source, 'sha256': 'a'*64, 'path': 'a.py'}
        messages = [{'role': 'system', 'content': 's'}, {'role': 'user', 'content': task},
                    {'role': 'assistant', 'tool_calls': call('read_file', path='a.py')['message']['tool_calls']},
                    {'role': 'tool', 'tool_name': 'read_file', 'content': json.dumps(read)}]
        original = deepcopy(messages)
        options = {'num_ctx': 8192, 'num_predict': 2048}
        output = build_context(messages, task, [], [], task, options, [], force=True)
        self.assertEqual(json.loads(output[-1]['content'])['text'], source)
        self.assertEqual(messages, original)
        self.assertLessEqual(tokens(output), input_budget(options, []))

    def test_replay_after_paging_replans_and_edits_the_middle(self):
        tools = SimonWorkTools(self.root, self.data, 'edit', lambda *a: False)
        self.addCleanup(tools.close)
        tools.auto_context = False
        source = 'A'*1800 + 'EXACT_EDIT_TARGET' + 'B'*5200
        (self.root / 'a.js').write_text(source)
        outer = self
        requests = []
        class Client:
            model = 'fixture'
            def request(self, path, data):
                requests.append(data)
                n = len(requests)
                if n == 1: return call('read_file', path='a.js')
                if n == 2: return call('read_file', path='a.js', char_offset=4000)
                if n == 3: return call('read_file', path='./a.js')
                if n == 4:
                    outer.assertNotIn('read_file', {t['function']['name'] for t in data['tools']})
                    replay = next(json.loads(m['content']) for m in reversed(data['messages']) if m.get('role') == 'tool')
                    outer.assertTrue(replay['cached'])
                    outer.assertEqual(replay['outcome'], 'success')
                    outer.assertEqual(replay['text'], source[:4000])
                    return call('edit_file', path='a.js', old_text='EXACT_EDIT_TARGET', new_text='FIXED',
                                expected_sha256=replay['edit_version'])
                outer.assertIn('read_file', {t['function']['name'] for t in data['tools']})
                return {'message': {'content': '已保存修改。'}}
        with patch.object(tools, 'read_file', wraps=tools.read_file) as read:
            result = run_work(Client(), '修改 a.js', [], tools, lambda: False, lambda _: None, lambda _: None)
        self.assertEqual(result['outcome'], 'completed')
        self.assertEqual(read.call_count, 2)
        self.assertEqual((self.root/'a.js').read_text(), source.replace('EXACT_EDIT_TARGET', 'FIXED'))

    def test_three_recoveries_separated_by_real_edits_are_not_a_loop(self):
        responses = []
        from hashlib import sha256
        version = sha256(b'old').hexdigest()
        for name in ('a.py', 'b.py', 'c.py'):
            (self.root/name).write_text('old')
            responses.extend([call('read_file', path=name), call('read_file', path=name),
                              call('edit_file', path=name, old_text='old', new_text='new', expected_sha256=version)])
        responses.append({'message': {'content': '已修正三个文件。'}})
        class Client:
            model = 'fixture'
            def request(self, *args): return responses.pop(0)
        result = run_work(Client(), '修改三个文件', [], self.tools, lambda: False, lambda _: None, lambda _: None)
        self.assertEqual(result['outcome'], 'completed')
        self.assertEqual(result['model_recovery']['repetition_recoveries'], 3)
        for name in ('a.py', 'b.py', 'c.py'): self.assertEqual((self.root/name).read_text(), 'new')

    def test_compacted_large_replay_keeps_narrow_read_available(self):
        tools = SimonWorkTools(self.root, self.data, 'small-context', lambda *a: False)
        self.addCleanup(tools.close)
        tools.auto_context = False
        tools.model_options = {'num_ctx': 8192, 'num_predict': 2048}
        source = '中'*2500 + 'CHANGE_ME' + '文'*3500
        (self.root/'a.py').write_text(source, encoding='utf-8')
        requests = []
        outer = self
        class Client:
            model = 'fixture'
            def request(self, path, data):
                requests.append(data)
                n = len(requests)
                if n in (1, 2): return call('read_file', path='a.py', max_chars=16000)
                if n == 3:
                    outer.assertIn('read_file', {t['function']['name'] for t in data['tools']})
                    return call('read_file', path='a.py', char_offset=2500, max_chars=256)
                if n == 4:
                    replay = next(json.loads(m['content']) for m in reversed(data['messages']) if m.get('role')=='tool')
                    outer.assertIn('CHANGE_ME', replay['text'])
                    return call('edit_file', path='a.py', old_text='CHANGE_ME', new_text='FIXED',
                                expected_sha256=replay['edit_version'])
                return {'message': {'content': '已保存。'}}
        result = run_work(Client(), '修改 a.py', [], tools, lambda: False, lambda _: None, lambda _: None)
        self.assertEqual(result['outcome'], 'completed')
        self.assertEqual((self.root/'a.py').read_text(encoding='utf-8'), source.replace('CHANGE_ME','FIXED'))

    def test_no_test_requirement_does_not_send_completed_edit_back_into_tools(self):
        tools = SimonWorkTools(self.root, self.data, 'validation', lambda *a: False)
        self.addCleanup(tools.close)
        steps = [{'tool':'edit_file', 'arguments':{'path':'style.css'}, 'result':{'outcome':'success'}}]
        for clause in ('不用运行测试', '不需要运行测试', '无需运行测试'):
            with self.subTest(clause=clause):
                self.assertIsNone(tools.completion_problem('修改 style.css，其余内容不变。'+clause+'。', steps))
        self.assertIsNotNone(tools.completion_problem('修改 style.css，运行测试。', steps))


if __name__ == '__main__':
    unittest.main()
