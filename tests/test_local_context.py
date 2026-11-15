from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import Mock

from guarded_agent.simon_work import SimonWorkTools
from guarded_agent.work_agent import run_work
from guarded_agent.chat_service import Controller
from guarded_agent.host_policy import read_policy


class LocalContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.workspace = self.base / 'workspace'; self.workspace.mkdir()
        self.catalog = self.base / 'projects'
        self.code = self.catalog / '01_AI' / 'SimonAI' / 'outputs' / 'assistant'
        self.code.mkdir(parents=True)
        (self.code / 'pyproject.toml').write_text('[project]\nname="fixture-assistant"')
        (self.code / 'README.md').write_text('SimonAI entry is desktop_app.pyw', encoding='utf-8')
        (self.code / '.env').write_text('DO_NOT_EXPOSE=this-is-secret')
        (self.workspace / 'README.md').write_text('Current task workspace')
        self.approve = Mock(return_value=False)

    def tools(self, full=True, auto=True):
        tool = SimonWorkTools(self.workspace, self.base / 'data', 'discovery', self.approve,
            full_access=full, auto_context=auto, projects_root=self.catalog, conversation_id='same')
        self.addCleanup(tool.close)
        return tool

    def test_discovery_reads_actual_entry_and_ignores_credentials(self):
        result = self.tools().execute('discover_projects', {'query': '继续 SimonAI'})
        self.assertEqual(result['projects'][0]['path'], str(self.code))
        raw = json.dumps(result, ensure_ascii=False)
        self.assertIn('desktop_app.pyw', raw)
        self.assertNotIn('DO_NOT_EXPOSE', raw)
        self.assertNotIn('this-is-secret', raw)
        self.approve.assert_not_called()

    def test_context_reaches_first_model_request_with_previous_task(self):
        first = self.tools(); first.checkpoint('previous saved objective', [], 'incomplete')
        tools = self.tools()
        class Client:
            model = 'fixture'
            def request(inner, endpoint, data):
                evidence = json.loads([m['content'] for m in data['messages'] if m['role'] == 'tool'][0])
                self.assertEqual(evidence['previous_task']['task'], 'previous saved objective')
                self.assertTrue(evidence['entry_documents'])
                return {'message': {'content': '已找到本机项目和之前任务。'}}
        result = run_work(Client(), '继续 SimonAI', [], tools, lambda: False, lambda e: None, lambda t: None)
        self.assertEqual(result['steps'][0]['tool'], 'discover_projects')
        self.assertEqual(result['outcome'], 'completed')
        self.approve.assert_not_called()

    def test_scoped_mode_never_reads_external_catalog(self):
        result = self.tools(full=False).execute('discover_projects', {'query': 'SimonAI'})
        self.assertEqual(result['projects'], [])
        self.assertNotIn('desktop_app.pyw', json.dumps(result))
        self.assertTrue(result['workspace_items'])

    def test_disabled_context_does_not_scan_and_invalid_args_cannot_enable_it(self):
        tools = self.tools(auto=False)
        self.assertEqual(tools.prepare_task('SimonAI'), [])
        with self.assertRaises(ValueError): tools.execute('discover_projects', {'query': '', 'full_access': True})

    def test_readonly_prohibition_is_not_mistaken_for_required_write(self):
        tools = self.tools()
        steps = tools.prepare_task('查看本地项目')
        self.assertIsNone(tools.completion_problem('查看本地项目，只读检查，不修改项目。不要运行 unittest 测试。', steps))
        self.assertIsNotNone(tools.completion_problem('不要修改代码，生成 README.md 文件。', steps))

    def test_web_entry_uses_host_policy_and_automatic_commands(self):
        directory = self.base / 'web'
        controller = Controller(directory)
        self.addCleanup(controller.close)
        conversation = controller.create_conversation(str(self.workspace))
        policy = {'auto_execute': True, 'full_access': True, 'auto_context': True,
                  'allow_web': False, 'projects_root': str(self.catalog), 'runtime_options': {'num_ctx': 8192}}
        path = directory / '.guarded-policy' / '.env'; path.parent.mkdir()
        path.write_text(json.dumps(policy))
        tool = controller.work_tools(conversation, 'webtest', self.approve)
        self.addCleanup(tool.close)
        self.assertIsInstance(tool, SimonWorkTools)
        self.assertTrue(controller.state(conversation)['settings']['auto_context'])
        result = tool.execute('run_command', {'command': 'Write-Output local-auto-ok'})
        self.assertEqual(result['outcome'], 'success')
        self.assertIn('local-auto-ok', result['output'])
        self.approve.assert_not_called()
        with self.assertRaises(ValueError): tool.path(str(path))
        policy['auto_execute'] = 'true'; path.write_text(json.dumps(policy))
        with self.assertRaises(ValueError): read_policy(directory)


if __name__ == '__main__': unittest.main()
