"""Regression coverage for the repeated-search failure seen in a real task."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from guarded_agent.work_tools import WorkTools
from guarded_agent.work_progress import WorkProgress
from guarded_agent.planning_context import build_context
from guarded_agent.simon_work import SimonWorkTools
from guarded_agent.work_agent import run_work


class SearchRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.tools = WorkTools(self.root, self.root / 'data', 'run', lambda *a: True)

    def test_github_query_uses_repository_search_and_preserves_encoding(self):
        payload = {'items': [{'full_name': 'owner/glass', 'html_url': 'https://github.com/owner/glass',
                              'description': 'CSS glass', 'language': 'CSS'}]}
        with patch('guarded_agent.work_tools.public_get', return_value={'text': json.dumps(payload)}) as get:
            result = self.tools.search_web('site:github.com liquid glass & css')
        url = urlsplit(get.call_args.args[0])
        self.assertEqual(url.hostname, 'api.github.com')
        self.assertEqual(parse_qs(url.query)['q'], ['liquid glass & css'])
        self.assertEqual(result['results'][0]['url'], 'https://github.com/owner/glass')
        self.assertEqual(result['outcome'], 'success')

    def test_empty_github_search_reports_failure_with_recovery_hint(self):
        with patch('guarded_agent.work_tools.public_get', return_value={'text': '{"items": []}' } ):
            result = self.tools.search_web('github mutually exclusive frameworks')
        self.assertEqual(result['outcome'], 'failed')
        self.assertIn('少量核心主题词', result['note'])

    def test_search_stall_is_detected_across_queries_and_plan_updates(self):
        progress = WorkProgress()
        result = {'results': [{'url': 'https://github.com/'}], 'outcome': 'success'}
        for i in range(3):
            progress.observe('search_web', {'query': str(i)}, result)
            progress.observe('update_plan', {}, {'plan': []})
        self.assertEqual(progress.repeated_searches, 2)
        progress.observe('fetch_url', {}, {'url': 'https://github.com/owner/glass', 'text': 'source'})
        self.assertEqual(progress.repeated_searches, 0)

    def test_matched_project_survives_checkpoint_and_context_window(self):
        target = {'name': 'Guarded', 'path': 'D:/installation/app'}
        discovery = {'tool': 'discover_projects', 'arguments': {'query': 'Guarded'},
                     'result': {'matched_projects': [target]}}
        search = {'tool': 'search_web', 'arguments': {'query': 'github glass'},
                  'result': {'results': [{'url': 'https://github.com/owner/glass'}]}}
        self.tools.checkpoint('修复 Guarded', [discovery, search], 'incomplete')
        restored = json.loads(self.tools.task_file.read_text(encoding='utf-8'))['steps']
        self.assertEqual(restored[0]['result']['matched_projects'], [target])
        self.assertEqual(restored[1]['result']['results'], search['result']['results'])
        steps = restored + [{'tool': 'list_files', 'arguments': {}, 'result': {}} for _ in range(30)]
        output = build_context([{'role': 'system', 'content': 's'}, {'role': 'user', 'content': '继续'}],
                               '继续', steps, [], '修复 Guarded', {'num_ctx': 8192, 'num_predict': 2048}, [], force=True)
        self.assertIn(target['path'], json.dumps(output, ensure_ascii=False))

    def test_discovery_matches_installed_guarded_instead_of_upload_copies(self):
        app = self.root / 'app'; app.mkdir()
        (app / 'pyproject.toml').write_text('[project]\nname="guarded"')
        catalog = self.root / 'projects'; catalog.mkdir()
        tools = SimonWorkTools(self.root, self.root / 'data2', 'match', lambda *a: False,
                               full_access=True, projects_root=catalog)
        self.addCleanup(tools.close)
        result = tools.execute('discover_projects', {'query': '把效果应用到guarded里'})
        self.assertEqual(result['matched_projects'][0]['path'], str(app))

    def test_loop_warns_before_hard_stall_and_allows_reading_source(self):
        def call(name, **args):
            return {'tool_calls': [{'function': {'name': name, 'arguments': args}}]}
        responses = iter([call('search_web', query='a'), call('search_web', query='b'),
                          call('search_web', query='c'), call('read_file', path='source.py'),
                          {'content': '已检查来源。'}])
        (self.root / 'source.py').write_text('source')
        captured = []
        class Client:
            model = 'fixture'
            def request(inner, endpoint, data):
                captured.append(json.dumps(data, ensure_ascii=False))
                return {'message': next(responses)}
        with patch.object(self.tools, 'search_web', return_value={'outcome': 'success', 'results': [{'url': 'https://github.com/'}]}):
            result = run_work(Client(), '检查状态', [], self.tools, lambda: False, lambda _: None, lambda _: None)
        self.assertEqual(result['outcome'], 'completed')
        self.assertIn('搜索没有新增证据', captured[3])
        self.assertEqual(result['steps'][-1]['tool'], 'read_file')
