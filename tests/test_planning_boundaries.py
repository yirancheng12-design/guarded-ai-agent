"""Regression coverage for shared Guarded/SimonAI planning boundaries."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from guarded_agent.work_agent import run_work


def call(name='list_files', arguments=None):
    return {'function': {'name': name, 'arguments': {} if arguments is None else arguments}}


class PlanningBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = 0
        self.tools = SimpleNamespace(root=Path(self.temp.name), plan=[], artifacts=[],
            checkpoint=Mock(), execute=Mock(return_value={'outcome': 'success'}), task_timeout=30)
        self.clock = patch('guarded_agent.work_agent.time.monotonic', lambda: self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def run_response(self, response, history=None, on_request=None):
        def request(*args):
            if on_request: on_request()
            return response
        client = SimpleNamespace(model='fixture', request=Mock(side_effect=request))
        return run_work(client, '任务', history or [], self.tools,
                        lambda: False, lambda event: None, lambda text: None, max_rounds=1)

    def test_model_finishing_at_deadline_cannot_dispatch(self):
        result = self.run_response({'message': {'tool_calls': [call('open_app')]}},
                                   on_request=lambda: setattr(self, 'now', 30))
        self.assertIn(result['outcome'], {'unknown', 'incomplete'})
        self.tools.execute.assert_not_called()

    def test_slow_first_tool_prevents_second_dispatch(self):
        def execute(*args):
            self.now = 31
            return {'outcome': 'success'}
        self.tools.execute.side_effect = execute
        result = self.run_response({'message': {'tool_calls': [call(), call('open_app')]}})
        self.assertEqual(self.tools.execute.call_count, 1)
        self.assertEqual(len(result['steps']), 1)
        self.assertIn('超时', result['text'])

    def test_malformed_later_call_prevents_partial_batch(self):
        for malformed in (None, {'function': []}, call([], {}), call('read_file', 'null'),
                          call('read_file', '{bad'), call('read_file', '[]')):
            with self.subTest(malformed=malformed):
                result = self.run_response({'message': {'tool_calls': [call('open_app'), malformed]}})
                self.assertEqual(result['outcome'], 'unknown')
                self.tools.execute.assert_not_called()

    def test_valid_json_arguments_still_dispatch(self):
        self.run_response({'message': {'tool_calls': [call('read_file', '{"path":"a.txt"}')]}})
        self.tools.execute.assert_called_once_with('read_file', {'path': 'a.txt'})

    def test_context_compaction_does_not_mutate_caller_history(self):
        history = [{'role': 'tool', 'content': 'evidence' * 1000}] + [
            {'role': 'user', 'content': 'follow up'} for _ in range(7)]
        original = deepcopy(history)
        result = self.run_response({'message': {'content': '答复'}}, history)
        self.assertEqual(result['outcome'], 'completed')
        self.assertEqual(history, original)


if __name__ == '__main__': unittest.main()
