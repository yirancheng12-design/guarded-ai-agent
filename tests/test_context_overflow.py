"""Regression cases for long-running requests and large tool exchanges."""
from copy import deepcopy
from io import BytesIO
from types import SimpleNamespace
import json
import unittest

from guarded_agent.context_budget import compact_result
from guarded_agent.planning_context import build_context, chat_context, input_budget, tokens
from guarded_agent.chat_service import chat_stream
from guarded_agent.ollama_agent import OllamaClient


class ContextOverflowTests(unittest.TestCase):
    options = {'num_ctx': 8192, 'num_predict': 2048}

    def test_large_last_write_keeps_outcome_without_resending_source(self):
        task = '修改 app.py 并保留全部现有测试'
        args = {'path': 'app.py', 'content': 'print(123)\n'*20000, 'expected_sha256': 'read9'}
        result = {'outcome': 'success', 'path': 'app.py', 'sha256': 'a'*64}
        messages = [{'role': 'system', 'content': 'system'}, {'role': 'user', 'content': task},
                    {'role': 'assistant', 'tool_calls': [{'function': {'name': 'write_file', 'arguments': args}}]},
                    {'role': 'tool', 'tool_name': 'write_file', 'content': json.dumps(result)},
                    {'role': 'user', 'content': '宿主提示：此前写入已成功，不能重复执行。'}]
        original = deepcopy(messages)
        output = build_context(messages, task, [{'tool': 'write_file', 'arguments': args, 'result': result}],
                               [], task, self.options, [])
        self.assertLessEqual(tokens(output), input_budget(self.options, []))
        self.assertEqual(messages, original)
        self.assertEqual(output[-1], messages[-1])
        raw = json.dumps(output, ensure_ascii=False)
        self.assertIn('write_file', raw)
        self.assertIn('success', raw)
        self.assertIn('不能重复执行', raw)
        self.assertNotIn(args['content'], raw)
        self.assertFalse(any(m.get('role') == 'tool' for m in output))

    def test_batch_json_compaction_has_a_hard_size_limit(self):
        value = {'files': [{'path': f'{i}.py', 'edit_version': f'read{i}', 'sha256': 'b'*64,
                           'text': '中文'*12000} for i in range(100)], 'outcome': 'success'}
        for limit in (128, 1000, 1800):
            with self.subTest(limit=limit):
                excerpt = compact_result(json.dumps(value, ensure_ascii=False), limit)
                self.assertLessEqual(len(excerpt), limit)
                json.loads(excerpt)
        excerpt = compact_result(json.dumps(value, ensure_ascii=False), 1800)
        self.assertIn('read0', excerpt)
        self.assertIn('b'*64, excerpt)
        self.assertLessEqual(len(compact_result('中文'*10000, 1000)), 1000)

    def test_many_nested_keys_cannot_escape_the_limit(self):
        value = {str(i): {str(j): ['x'*900]*60 for j in range(40)} for i in range(12)}
        compact = compact_result(json.dumps(value), 1000)
        self.assertLessEqual(len(compact), 1000)
        json.loads(compact)

    def test_recent_tool_batch_is_kept_whole(self):
        task = '继续修复'
        messages = [{'role': 'system', 'content': 's'},
                    {'role': 'assistant', 'content': '旧记录'*20000},
                    {'role': 'user', 'content': task},
                    {'role': 'assistant', 'tool_calls': [
                        {'function': {'name': 'read_file', 'arguments': {'path': p}}} for p in ('a.py', 'b.py')]},
                    {'role': 'tool', 'tool_name': 'read_file', 'content': '{"edit_version":"read1"}'},
                    {'role': 'tool', 'tool_name': 'read_file', 'content': '{"edit_version":"read2"}'}]
        output = build_context(messages, task, [], [], '修复功能', self.options, [])
        self.assertEqual(output[-3:], messages[-3:])
        self.assertLessEqual(tokens(output), input_budget(self.options, []))

    def test_large_plan_is_excerpted_and_marked(self):
        task = '继续'
        messages = [{'role': 'system', 'content': 's'}, {'role': 'user', 'content': task},
                    {'role': 'assistant', 'content': 'x'*90000}]
        plan = [{'step': '中文'*4000, 'status': 'pending'}]*80
        output = build_context(messages, task, [], plan, '原始目标', self.options, [])
        self.assertLessEqual(tokens(output), input_budget(self.options, []))
        self.assertIn('原始目标', json.dumps(output, ensure_ascii=False))
        self.assertEqual(len(plan), 80)

    def test_chat_budget_handles_cjk_and_retains_current_input(self):
        messages = [{'role': 'system', 'content': '聊天规则'}] + [
            {'role': 'user' if i%2 == 0 else 'assistant', 'content': '旧消息'*1000} for i in range(20)] + [
            {'role': 'user', 'content': '解释刚才的结果'}]
        output = chat_context(messages, self.options)
        self.assertEqual(output[-1], messages[-1])
        self.assertEqual(output[0], messages[0])
        self.assertLessEqual(tokens(output), input_budget(self.options, []))
        with self.assertRaisesRegex(ValueError, '拆分消息'):
            chat_context(messages[:1]+[{'role': 'user', 'content': '指令'*20000}], self.options)

    def test_stream_applies_budget_to_actual_request(self):
        captured = []
        client = OllamaClient('test')
        def open_request(request, **kwargs):
            captured.append(json.loads(request.data))
            return BytesIO(b'{"message":{"content":"ok"},"done":true}\n')
        client.opener = SimpleNamespace(open=open_request)
        history = [{'role': 'user', 'content': '旧消息'*20000}, {'role': 'user', 'content': '你好'}]
        result = chat_stream(client, history, lambda: False, lambda _: None)
        self.assertEqual(result['text'], 'ok')
        request = captured[0]
        self.assertLessEqual(tokens(request['messages']), input_budget(request['options'], []))
        self.assertEqual(request['messages'][-1], history[-1])


if __name__ == '__main__':
    unittest.main()
