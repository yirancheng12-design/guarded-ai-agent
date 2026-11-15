"""Anti-spin regressions: semantic evidence, bounded recovery and no replay."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from guarded_agent.work_tools import WorkTools
from guarded_agent.simon_work import SimonWorkTools
from guarded_agent.work_agent import run_work
from guarded_agent.work_progress import WorkProgress, pending_action


def call(name, **arguments):
    return {'message': {'tool_calls': [{'function': {'name': name, 'arguments': arguments}}]}}


class IdleLoopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/'workspace'; self.root.mkdir()
        self.tools = WorkTools(self.root, Path(self.temp.name)/'data', 'idle', lambda *a: False)
        self.events = []

    def run_responses(self, responses, task='完成任务', history=None):
        class Client:
            model = 'fixture'
            def __init__(inner): inner.requests = []
            def request(inner, path, data):
                inner.requests.append(data)
                return responses[len(inner.requests)-1]
        self.client = Client()
        return run_work(self.client, task, history or [], self.tools, lambda: False,
                        self.events.append, lambda text: None)

    def test_reworded_empty_search_stops_and_recovers_once(self):
        with patch.object(self.tools, 'execute', return_value={'outcome': 'success', 'results': []}) as execute:
            result = self.run_responses([call('search_web', query=f'variant {i}') for i in range(50)])
        self.assertEqual(result['outcome'], 'incomplete')
        self.assertLessEqual(execute.call_count, 14)
        self.assertEqual(result['model_recovery']['stalled_recoveries'], 1)
        self.assertTrue(any(e.get('reason') == 'no_new_evidence' for e in self.events))

    def test_plan_rewrites_and_task_status_are_not_progress(self):
        p = WorkProgress()
        for i in range(15):
            p.observe('update_plan', {'steps': [i]}, {'plan': [i], 'outcome': 'success'})
            p.observe('task_status', {}, {'previous': {'steps': [i], 'updated': i}})
        self.assertEqual(p.stale_steps, 30)

    def test_search_rewording_snippet_and_order_do_not_reset_stall(self):
        p = WorkProgress()
        for i in range(15):
            p.observe('search_web', {'query': str(i)}, {'outcome': 'success', 'query': str(i),
                'results': [{'url': 'https://example.com/a', 'snippet': str(i)}]})
        self.assertEqual(p.stale_steps, 14)
        p.observe('fetch_url', {'url': 'https://example.com/a'}, {'text': 'new source content'})
        self.assertEqual(p.stale_steps, 0)

    def test_real_new_pages_and_changed_file_versions_reset_stall(self):
        p = WorkProgress()
        for i in range(50):
            p.observe('search_web', {'query': str(i)}, {'results': [{'url': f'https://example.com/{i}'}]})
            self.assertEqual(p.stale_steps, 0)
        p.observe('read_file', {'path': 'a.py'}, {'path': 'a.py', 'sha256': 'old', 'text': 'old'})
        before = p.revision
        p.observe('read_file', {'path': 'a.py'}, {'path': 'a.py', 'sha256': 'new', 'text': 'new'})
        self.assertGreater(p.revision, before)

    def test_explicit_promise_recovers_into_real_file_write(self):
        responses = [{'message': {'content': '我将创建文件并保存结果。'}},
                     call('write_file', path='result.txt', content='saved', expected_sha256=''),
                     {'message': {'content': '已保存 result.txt。'}}]
        result = self.run_responses(responses, '创建结果文件')
        self.assertEqual(result['outcome'], 'completed')
        self.assertEqual((self.root/'result.txt').read_text(), 'saved')
        self.assertEqual(result['model_recovery']['idle_recoveries'], 1)
        self.assertEqual(self.client.requests[1]['options']['temperature'], 0)
        self.assertLessEqual(self.client.requests[1]['options']['num_predict'], 1024)

    def test_repeated_promises_end_incomplete(self):
        result = self.run_responses([{'message': {'content': '接下来我将读取文件并执行修改。'}}]*3)
        self.assertEqual(result['outcome'], 'incomplete')
        self.assertEqual(len(self.client.requests), 3)
        self.assertEqual(result['steps'], [])

    def test_explanations_and_explicit_blockers_do_not_trigger_actions(self):
        self.assertFalse(pending_action('我会运行测试来检查。', '解释如何进行检查'))
        self.assertFalse(pending_action('我将读取文件，但缺少具体路径，请提供文件名。', '修复文件'))
        result = self.run_responses([{'message': {'content': '缺少具体路径，请提供文件名。'}}])
        self.assertEqual(len(self.client.requests), 1)
        self.assertEqual(result['steps'], [])

    def test_separate_empty_episodes_can_recover_after_progress(self):
        responses = []
        for i in range(3):
            responses += [{'message': {}}, call('read_file', path=f'{i}.txt')]
            (self.root/f'{i}.txt').write_text(str(i))
        responses += [{'message': {'content': '已读取三个文件。'}}]
        result = self.run_responses(responses)
        self.assertEqual(result['outcome'], 'completed')
        self.assertEqual(result['model_recovery']['empty_retries'], 3)
        self.assertEqual(len(result['steps']), 3)

    def test_successful_command_is_not_replayed_after_interleaved_read(self):
        (self.root/'a.txt').write_text('a')
        responses = [call('run_command', command='some command'), call('read_file', path='a.txt'),
                     call('run_command', command='some command'), {'message': {'content': '已检查。'}}]
        with patch.object(self.tools, 'run_command', return_value={'outcome': 'success', 'output': 'done'}) as run:
            result = self.run_responses(responses)
        run.assert_called_once()
        self.assertEqual(result['outcome'], 'completed')

    def test_continuation_validates_the_original_objective(self):
        tools = SimonWorkTools(self.root, Path(self.temp.name)/'simon', 'continue', lambda *a: False)
        self.addCleanup(tools.close); self.tools = tools
        tools.task_state = {'objective': '创建 app.py 文件', 'task': '创建 app.py 文件', 'steps': [], 'plan': []}
        result = self.run_responses([{'message': {'content': '完成了。'}}]*2, task='继续')
        self.assertEqual(result['outcome'], 'incomplete')
        self.assertIn('没有成功', result['text'])
        self.assertFalse((self.root/'app.py').exists())

    def test_unknown_action_is_never_recovered(self):
        with patch.object(self.tools, 'execute', return_value={'outcome': 'unknown', 'execution_status': 'sent'}) as execute:
            result = self.run_responses([call('run_command', command='some command')])
        self.assertEqual(result['outcome'], 'unknown')
        self.assertEqual(len(self.client.requests), 1)
        execute.assert_called_once()

    def test_resume_does_not_repeat_a_previously_failed_command(self):
        self.tools.task_state = {'objective': '检查状态', 'task': '检查状态', 'plan': [], 'steps': [
            {'tool': 'run_command', 'arguments': {'command': 'same test'},
             'result': {'outcome': 'failed', 'exit_code': 1, 'output': 'failure'}}]}
        with patch.object(self.tools, 'run_command') as run:
            result = self.run_responses([call('run_command', command='same test'),
                {'message': {'content': '先前测试失败，尚未修复。'}}], task='继续')
        run.assert_not_called()
        self.assertEqual(result['model_recovery']['repetition_recoveries'], 1)

    def test_empty_recovery_allows_testing_after_a_real_file_change(self):
        (self.root/'a.py').write_text('old')
        from hashlib import sha256
        version = sha256(b'old').hexdigest()
        responses = [call('read_file', path='a.py'), call('run_command', command='test'),
                     {'message': {}},
                     call('edit_file', path='a.py', old_text='old', new_text='new', expected_sha256=version),
                     call('run_command', command='test'), {'message': {'content': '验证完成。'}}]
        with patch.object(self.tools, 'run_command', return_value={'outcome': 'success', 'exit_code': 0}) as run:
            result = self.run_responses(responses)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(result['outcome'], 'completed')


if __name__ == '__main__': unittest.main()
