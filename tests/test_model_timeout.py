"""Slow local inference must not inherit the short discovery timeout."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from guarded_agent.ollama_agent import OllamaClient
from guarded_agent.chat_service import chat_stream


class ModelTimeoutTests(unittest.TestCase):
    def work_client(self, items):
        client = OllamaClient('test')
        client.opener = MagicMock()
        client.opener.open.return_value.__enter__.return_value = BytesIO(
            b''.join(json.dumps(item).encode()+b'\n' for item in items))
        return client

    def test_work_stream_assembles_response_only_after_done(self):
        client = self.work_client([{'message': {'thinking': 'reason'}},
            {'message': {'content': 'ok'}}, {'done': True, 'eval_count': 2}])
        result = client.request_work({'options': {}}, lambda: False, lambda _: None)
        self.assertEqual(result['message']['content'], 'ok')
        self.assertEqual(result['message']['thinking'], 'reason')
        self.assertEqual(result['eval_count'], 2)
        self.assertEqual(json.loads(client.opener.open.call_args.args[0].data)['options']['draft_num_predict'], 0)

    def test_partial_tool_response_is_discarded_on_disconnect(self):
        client = self.work_client([{'message': {'tool_calls': [{'function': {'name': 'write_file'}}]}}])
        with self.assertRaises(ConnectionError):
            client.request_work({}, lambda: False, lambda _: None)

    def test_cancelled_work_stream_never_returns_pending_calls(self):
        client = self.work_client([{'message': {'tool_calls': [{'function': {'name': 'write_file'}}]}, 'done': True}])
        with self.assertRaises(InterruptedError):
            client.request_work({}, lambda: True, lambda _: None)

    def test_work_stream_enforces_absolute_generation_budget(self):
        client = self.work_client([{'message': {'content': 'late'}, 'done': True}])
        with patch('guarded_agent.ollama_agent.time.monotonic', side_effect=[0, 0, 181]):
            with self.assertRaises(TimeoutError):
                client.request_work({}, lambda: False, lambda _: None, timeout=180)

    def test_generation_batch_preserves_context_overrides_and_input(self):
        client = OllamaClient('qwen3.8-agent:27b')
        client.opener = MagicMock()
        client.opener.open.return_value.__enter__.side_effect = [
            BytesIO(b'{"done":true}'), BytesIO(b'{"done":true}')]
        data = {'options': {'num_ctx': 16384, 'num_predict': 4096}}
        client.request('/api/chat', data)
        sent = json.loads(client.opener.open.call_args.args[0].data)
        self.assertEqual(sent['options'], {'num_ctx': 16384, 'num_predict': 4096, 'num_batch': 128, 'draft_num_predict': 0})
        self.assertNotIn('num_batch', data['options'])
        client.request('/api/chat', {'options': {'num_batch': 64}})
        self.assertEqual(json.loads(client.opener.open.call_args.args[0].data)['options']['num_batch'], 64)

    def test_generation_and_discovery_use_separate_timeouts(self):
        client = OllamaClient('gemma4:31b')
        client.opener = MagicMock()
        client.opener.open.return_value.__enter__.side_effect = [
            BytesIO(b'{"message":{"content":"ok"},"done":true}'),
            BytesIO(b'{"models":[]}'),
            BytesIO(b'{"done":true}'),
        ]
        client.request('/api/chat', {'stream': False})
        client.models()
        client.request('/api/chat', {}, timeout=7)
        self.assertEqual([c.kwargs['timeout'] for c in client.opener.open.call_args_list],
                         [1200, 5, 7])

    def test_chat_stream_uses_large_model_budget(self):
        client = OllamaClient('gemma4:31b')
        client.opener = MagicMock()
        client.opener.open.return_value.__enter__.return_value = BytesIO(
            b'{"message":{"content":"ok"},"done":true}\n')
        self.assertEqual(chat_stream(client, [], lambda: False, lambda _: None)['text'], 'ok')
        self.assertEqual(client.opener.open.call_args.kwargs['timeout'], 1200)
        sent = json.loads(client.opener.open.call_args.args[0].data)
        self.assertEqual(sent['options']['num_batch'], 128)

    def test_real_delayed_response_and_explicit_deadline(self):
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                time.sleep(.25)
                body = json.dumps({'message': {'content': 'ok'}, 'done': True}).encode()
                try:
                    self.send_response(200)
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = OllamaClient('gemma4:31b', f'http://127.0.0.1:{server.server_port}')
            with self.assertRaises(TimeoutError):
                client.request('/api/chat', {}, timeout=.05)
            self.assertEqual(client.request('/api/chat', {})['message']['content'], 'ok')
        finally:
            server.shutdown()
            server.server_close()
