import tempfile
import unittest
from pathlib import Path
from guarded_agent.work_agent import run_work
from guarded_agent.work_tools import WorkTools


def call(name, **args):
    return {'message': {'tool_calls': [{'function': {'name': name, 'arguments': args}}]}}


class EmptyResponseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/'workspace'
        self.root.mkdir()
        self.tools = WorkTools(self.root, Path(self.temp.name)/'data', 'empty', lambda *args:False)
        self.events = []

    def run_responses(self, responses):
        class Client:
            model = 'fixture'
            def __init__(self): self.requests = []
            def request(inner, path, data):
                inner.requests.append(data)
                return responses[len(inner.requests)-1]
        self.client = Client()
        return run_work(self.client, '完成任务', [], self.tools, lambda:False, self.events.append, lambda text:None)

    def test_whitespace_recovers_without_any_tool(self):
        result = self.run_responses([{'message':{'content':' \n'}}, {'message':{'content':'有效答复'}}])
        self.assertEqual(result['outcome'], 'completed')
        self.assertEqual(result['model_recovery']['empty_retries'], 1)
        self.assertEqual(result['steps'], [])
        self.assertTrue(any(e['kind']=='model_retry' for e in self.events))

    def test_persistent_empty_is_bounded_and_not_success(self):
        result = self.run_responses([{'message':{'thinking':'PRIVATE_REASONING'}}]*3)
        self.assertEqual(result['outcome'], 'incomplete')
        self.assertEqual(len(self.client.requests), 3)
        self.assertNotIn('PRIVATE_REASONING', str(result))

    def test_prior_write_is_preserved_without_reexecution(self):
        write = call('write_file', path='result.txt', content='saved', expected_sha256='')
        result = self.run_responses([write, {'message':{}}, {'message':{'content':'完成'}}])
        self.assertEqual(result['outcome'], 'completed')
        self.assertEqual(len(result['steps']), 1)
        self.assertEqual((self.root/'result.txt').read_text(), 'saved')
        self.assertTrue(any(m['role']=='tool' for m in self.client.requests[-1]['messages']))

    def test_recovery_cannot_replay_successful_write(self):
        write = call('write_file', path='result.txt', content='saved', expected_sha256='')
        result = self.run_responses([write, {'message':{}}, write])
        self.assertEqual(result['outcome'], 'incomplete')
        self.assertEqual(len(result['steps']), 1)

    def test_unknown_action_never_reaches_model_recovery(self):
        self.tools.execute = lambda *args: {'outcome':'unknown','execution_status':'sent'}
        result = self.run_responses([call('run_command', command='anything')])
        self.assertEqual(result['outcome'], 'unknown')
        self.assertEqual(len(self.client.requests), 1)
        self.assertEqual(result['model_recovery']['empty_retries'], 0)

    def test_cancel_during_recovery_does_not_request_model_again(self):
        stopped = []
        class Client:
            model = 'fixture'
            calls = 0
            def request(self, *args):
                self.calls += 1
                return {'message':{}}
        client = Client()
        def event(value):
            if value['kind'] == 'model_retry': stopped.append(True)
        result = run_work(client, '任务', [], self.tools, lambda:bool(stopped), event, lambda text:None)
        self.assertEqual(result['outcome'], 'cancelled')
        self.assertEqual(client.calls, 1)
