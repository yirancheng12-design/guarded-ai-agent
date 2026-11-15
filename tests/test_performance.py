import json
from pathlib import Path
import tempfile
import unittest
from guarded_agent.context_budget import compact_result
from guarded_agent.simon_work import SimonWorkTools
from guarded_agent.work_agent import run_work


class PerformanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/'workspace'
        self.root.mkdir()
        self.tools = SimonWorkTools(self.root, Path(self.temp.name)/'data', 'perf', lambda *args: False)
        self.addCleanup(self.tools.close)

    def test_batch_preserves_file_versions_and_stale_write_guard(self):
        for name in ('a.txt', 'b.txt'): (self.root/name).write_text(name)
        result = self.tools.execute('read_files', {'paths': ['a.txt', 'b.txt', 'a.txt']})
        self.assertEqual(len(result['files']), 2)
        a, b = result['files']
        self.assertNotEqual(a['edit_version'], b['edit_version'])
        (self.root/'a.txt').write_text('changed outside')
        with self.assertRaises(ValueError):
            self.tools.execute('write_file', {'path':'a.txt','content':'overwrite','expected_sha256':a['edit_version']})
        self.assertEqual((self.root/'a.txt').read_text(), 'changed outside')

    def test_batch_keeps_errors_and_protects_credentials(self):
        (self.root/'a.txt').write_text('ordinary')
        (self.root/'.env').write_text('SECRET_SENTINEL')
        result = self.tools.execute('read_files', {'paths':['a.txt','missing.txt','.env']})
        self.assertEqual(result['outcome'], 'failed')
        self.assertEqual(result['files'][0]['text'], 'ordinary')
        self.assertNotIn('SECRET_SENTINEL', json.dumps(result))
        for args in ({'paths':[]}, {'paths':['a.txt']*9}, {'paths':['a.txt'],'full_access':True}):
            with self.assertRaises(ValueError): self.tools.execute('read_files', args)

    def test_compaction_preserves_json_metadata_and_failure_tail(self):
        original = {'outcome':'failed','exit_code':1,'sha256':'a'*64,'edit_version':'read12',
                    'output':'x'*8000+'AssertionError: expected 33', 'path':'code.py'}
        compact = compact_result(json.dumps(original))
        result = json.loads(compact)
        for key in ('outcome','exit_code','sha256','edit_version','path'):
            self.assertEqual(result[key], original[key])
        self.assertIn('AssertionError: expected 33', result['output'])
        self.assertLess(len(compact), 1200)
        self.assertEqual(compact_result(compact), compact)

    def test_long_context_preserves_objective_versions_and_failure_tail(self):
        self.tools.task_objective = '整理文件并上传，不修改功能'
        messages = [{'role': 'system', 'content': 'system'}] + [
            {'role': 'user', 'content': '较早的上下文' * 1000} for _ in range(20)] + [
            {'role': 'user', 'content': '继续'}]
        steps = [{'tool': 'run_command', 'arguments': {'command': 'test'},
                  'result': {'outcome': 'failed', 'exit_code': 1,
                             'output': 'x' * 5000 + 'AssertionError: 0 != 2'}},
                 {'tool': 'read_file', 'arguments': {'path': 'a.py', 'start_line': 130},
                  'result': {'path': 'a.py', 'sha256': 'a' * 64, 'edit_version': 'read12'}}]
        compact = self.tools.planning_messages(messages, '继续', steps)
        evidence = json.loads(compact[2]['content'].split('：', 1)[1])
        self.assertEqual(evidence['objective'], self.tools.task_objective)
        self.assertIn('AssertionError: 0 != 2', evidence['evidence'][0]['result']['output'])
        self.assertEqual(evidence['evidence'][1]['result']['edit_version'], 'read12')
        self.assertEqual(evidence['evidence'][1]['result']['sha256'], 'a' * 64)
        self.assertEqual(evidence['evidence'][1]['arguments']['start_line'], 130)

    def test_real_loop_reports_model_usage(self):
        class Client:
            model = 'fixture'
            def request(self, *args):
                return {'message':{'content':'这里是解释'}, 'prompt_eval_count':123, 'eval_count':12}
        result = run_work(Client(), '解释这个项目', [], self.tools, lambda:False, lambda e:None, lambda t:None)
        self.assertEqual(result['performance']['model_calls'], 1)
        self.assertEqual(result['performance']['prompt_tokens'], 123)
        self.assertEqual(result['performance']['generated_tokens'], 12)
        self.assertEqual(result['outcome'], 'completed')
