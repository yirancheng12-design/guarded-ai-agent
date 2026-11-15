from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
import json

from guarded_agent.runtime_options import LIMITS, apply_options, validated_options
from guarded_agent.simon_work import SimonWorkTools
from guarded_agent.work_agent import run_work


class RuntimeOptionsTests(unittest.TestCase):
    def test_unlimited_count_defaults_survive_json_and_application(self):
        options = validated_options({})
        self.assertIsNone(options['max_rounds'])
        self.assertIsNone(options['max_tool_calls'])
        self.assertIsNone(options['task_timeout'])
        toolbox = SimpleNamespace()
        apply_options(toolbox, json.loads(json.dumps(options)))
        self.assertIsNone(toolbox.max_rounds)
        self.assertIsNone(toolbox.max_tool_calls)

    def test_null_only_means_unlimited_for_count_options(self):
        for values in ({'temperature': None}, {'max_rounds': float('inf')},
                       {'max_rounds': 'unlimited'}, {'max_tool_calls': True}):
            with self.subTest(values=values), self.assertRaises(ValueError): validated_options(values)

    def test_invalid_values_are_rejected(self):
        for values in ({'other': 1}, {'num_ctx': True}, {'temperature': float('nan')}, {'max_rounds': 0},
                       {'command_timeout': LIMITS['command_timeout'][2] + 1}, {'num_ctx': 2048, 'num_predict': 4096}):
            with self.subTest(values=values), self.assertRaises(ValueError): validated_options(values)

    def test_command_timeout_accepts_current_upper_bound(self):
        maximum = LIMITS['command_timeout'][2]
        self.assertEqual(validated_options({'command_timeout': maximum})['command_timeout'], maximum)

    def test_real_loop_honors_model_options_and_tool_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'work').mkdir()
            tools = SimonWorkTools(root / 'work', root / 'data', 'runtime', Mock(return_value=False))
            self.addCleanup(tools.close)
            apply_options(tools, {'num_ctx': 8192, 'num_predict': 512, 'temperature': .2, 'top_p': .8, 'max_tool_calls': 1})
            class Client:
                model = 'fixture'
                def request(inner, endpoint, request):
                    self.assertEqual(request['options'], {'num_ctx': 8192, 'num_predict': 512, 'temperature': .2, 'top_p': .8})
                    return {'message': {'tool_calls': [{'function': {'name': 'list_files', 'arguments': {'path': '.'}}}] * 2}}
            result = run_work(Client(), '列出文件', [], tools, lambda: False, lambda e: None, lambda e: None)
            self.assertEqual(len(result['steps']), 1)
            self.assertEqual(result['outcome'], 'unknown')
            self.assertIn('工具调用上限', result['text'])

    def test_command_timeout_is_used_by_real_process(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'work').mkdir()
            tools = SimonWorkTools(root / 'work', root / 'data', 'timeout', Mock(return_value=False), full_access=True)
            self.addCleanup(tools.close)
            apply_options(tools, {'command_timeout': 1})
            result = tools.execute('run_command', {'command': 'Start-Sleep -Seconds 10'})
            self.assertEqual(result['outcome'], 'unknown')
            self.assertEqual(result['reason'], 'interrupted_or_timeout_no_retry')
            blocked = tools.execute('run_command', {'command': 'Start-Sleep -Seconds 10'})
            self.assertEqual(blocked['reason'], 'unresolved_command_no_retry')


if __name__ == '__main__': unittest.main()
