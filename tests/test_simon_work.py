from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.request
from urllib.error import HTTPError

from guarded_agent.simon_work import SimonWorkTools, automatic_command
from guarded_agent.work_tools import Declined
from guarded_agent.work_preview import WorkspacePreview
from guarded_agent.work_agent import run_work


class WorkIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / 'project'
        self.root.mkdir()
        self.review = Mock(return_value=False)
        self.tools = SimonWorkTools(self.root, base / 'data', 'testwork', self.review,
                                    conversation_id='conversation', allow_web=False)
        self.addCleanup(self.tools.close)

    def test_files_edits_backup_and_real_test_command_without_confirmation(self):
        self.tools.execute('write_file', {'path': 'maths.py', 'content': 'def add(a,b):\n    return a-b\n', 'expected_sha256': ''})
        self.tools.execute('write_file', {'path': 'tests/test_maths.py', 'content':
            'import unittest\nfrom maths import add\nclass Tests(unittest.TestCase):\n    def test_add(self): self.assertEqual(add(2,3),5)\n', 'expected_sha256': ''})
        failed = self.tools.execute('run_command', {'command': 'python -m unittest discover -s tests -v'})
        self.assertEqual(failed['outcome'], 'failed')
        read = self.tools.execute('read_file', {'path': 'maths.py'})
        edited = self.tools.execute('edit_file', {'path': 'maths.py', 'old_text': 'a-b', 'new_text': 'a+b', 'expected_sha256': read['sha256']})
        self.assertIn('a-b', Path(edited['backup']).read_text())
        passed = self.tools.execute('run_command', {'command': 'python -m unittest discover -s tests -v'})
        self.assertEqual(passed['exit_code'], 0)
        self.assertIn('OK', passed['output'])
        self.review.assert_not_called()

    def test_command_policy_rejects_shell_injection_and_external_scripts(self):
        (self.root / 'test.py').write_text('print(1)')
        self.assertTrue(automatic_command('python test.py', self.tools))
        self.assertTrue(automatic_command('mkdir tests', self.tools))
        for command in ('python -c "print(1)"', 'python test.py; Remove-Item x',
                        'python test.py (Get-Content secret)', 'python test.py $(whoami)',
                        'python ../outside.py', 'python test.py > output.txt',
                        'pip install unknown', 'git push', 'Remove-Item -Recurse .',
                        'mkdir ../outside', 'mkdir D:/outside', 'mkdir *', 'mkdir -Force', 'mkdir a b'):
            self.assertFalse(automatic_command(command, self.tools), command)

    def test_unrecognized_command_denied_before_process(self):
        with patch.object(self.tools, '_run_approved_command') as execute:
            with self.assertRaises(Declined): self.tools.execute('run_command', {'command': 'Remove-Item file.txt'})
            execute.assert_not_called()

    def test_scoped_files_and_stale_hash(self):
        for path in ('../outside.txt', '.env', '.ssh/id_rsa'):
            with self.assertRaises(ValueError): self.tools.execute('write_file', {'path': path, 'content': 'x', 'expected_sha256': ''})
        result = self.tools.execute('write_file', {'path': 'safe.txt', 'content': 'first', 'expected_sha256': ''})
        (self.root / 'safe.txt').write_text('external edit')
        with self.assertRaises(ValueError):
            self.tools.execute('write_file', {'path': 'safe.txt', 'content': 'overwrite', 'expected_sha256': result['sha256']})
        self.assertEqual((self.root / 'safe.txt').read_text(), 'external edit')

    def test_short_read_version_keeps_hash_and_file_binding(self):
        self.tools.execute('write_file', {'path': 'a.txt', 'content': 'before', 'expected_sha256': ''})
        self.tools.execute('write_file', {'path': 'b.txt', 'content': 'other', 'expected_sha256': ''})
        read = self.tools.execute('read_file', {'path': 'a.txt'})
        with self.assertRaises(ValueError):
            self.tools.execute('write_file', {'path': 'b.txt', 'content': 'wrong', 'expected_sha256': read['edit_version']})
        changed = self.tools.execute('edit_file', {'path': 'a.txt', 'old_text': 'before', 'new_text': 'after', 'expected_sha256': read['edit_version']})
        self.assertEqual((self.root / 'a.txt').read_text(), 'after')
        self.assertIn('before', Path(changed['backup']).read_text())
        with self.assertRaises(ValueError):
            self.tools.execute('write_file', {'path': 'a.txt', 'content': 'stale', 'expected_sha256': read['edit_version']})

    def test_disabled_web_not_exposed_or_executable(self):
        self.assertNotIn('search_web', [t['function']['name'] for t in self.tools.tool_specs])
        for name, arguments in [('search_web', {'query': 'x'}), ('fetch_url', {'url': 'https://example.com'}),
                                ('browser_open', {'url': 'https://example.com'})]:
            with self.assertRaises(ValueError): self.tools.execute(name, arguments)
        self.assertIsNone(self.tools._service)

    def test_preview_blocks_private_files_escape_and_non_get(self):
        (self.root / 'index.html').write_text('<h1>preview</h1>')
        (self.root / '.env').write_text('must not serve')
        preview = WorkspacePreview(self.tools)
        try:
            with urllib.request.urlopen(preview.url('index.html')) as response:
                self.assertIn('preview', response.read().decode())
                self.assertIn("connect-src 'none'", response.headers['Content-Security-Policy'])
            for suffix in ('.env', '../outside.txt'):
                with self.assertRaises(HTTPError) as caught: urllib.request.urlopen(preview.url(suffix))
                caught.exception.close()
            with self.assertRaises(HTTPError) as caught: urllib.request.urlopen(preview.origin + '/index.html')
            caught.exception.close()
            with self.assertRaises(HTTPError) as caught:
                urllib.request.urlopen(urllib.request.Request(preview.url('index.html'), b'x', method='POST'))
            caught.exception.close()
        finally: preview.close()

    def test_preview_real_browser_fill_click_screenshot(self):
        from importlib.util import find_spec
        if find_spec('playwright') is None:
            self.skipTest('Install Playwright and Chromium to run browser integration tests')
        self.tools.execute('write_file', {'path': 'index.html', 'content':
            '<label>摄氏<input id="t" type="number"></label><button onclick="out.textContent=Number(t.value)*1.8+32">换算</button><p id="out">等待</p>',
            'expected_sha256': ''})
        page = self.tools.execute('preview_file', {'path': 'index.html'})
        self.assertEqual(page['outcome'], 'success')
        input_id = next(e['id'] for e in page['elements'] if e['tag'] == 'input')
        result = self.tools.execute('browser_action', {'tab_id': page['tab_id'], 'snapshot_id': page['snapshot_id'],
            'element_id': input_id, 'kind': 'fill', 'value': '25', 'condition': {'property': 'value', 'expected': '25'}})
        self.assertEqual(result['outcome'], 'success')
        page = self.tools.execute('browser_read', {'tab_id': page['tab_id']})
        button_id = next(e['id'] for e in page['elements'] if e['tag'] == 'button')
        rejected = self.tools.execute('browser_action', {'tab_id': page['tab_id'], 'snapshot_id': page['snapshot_id'],
            'element_id': button_id, 'kind': 'click', 'value': '', 'condition': {'property': 'text', 'expected': '换算'}})
        self.assertEqual(rejected['execution_status'], 'not_sent')
        self.assertEqual(rejected['outcome'], 'failed')
        page = self.tools.execute('browser_read', {'tab_id': page['tab_id']})
        self.assertNotIn('77', page['text'])
        button_id = next(e['id'] for e in page['elements'] if e['tag'] == 'button')
        result = self.tools.execute('browser_action', {'tab_id': page['tab_id'], 'snapshot_id': page['snapshot_id'],
            'element_id': button_id, 'kind': 'click', 'value': '', 'condition': {'property': 'text', 'expected': '77'}})
        self.assertEqual(result['samples'], 3)
        page = self.tools.execute('browser_read', {'tab_id': page['tab_id']})
        self.assertIn('77', page['text'])
        self.assertTrue(Path(page['screenshot']).is_file())
        self.review.assert_not_called()

    def test_preview_cannot_navigate_away_when_web_disabled(self):
        self.tools._preview = Mock()
        self.tools._preview.owns.side_effect = lambda url: url == 'http://127.0.0.1:12345/owned'
        with self.assertRaises(ValueError):
            self.tools.review('执行网页操作', {'url': 'http://127.0.0.1:12345/owned', 'kind': 'click', 'value': '',
                                             'element': {'tag': 'a', 'href': 'https://example.com', 'name': 'read'}})

    def test_stop_before_tool_has_no_side_effect(self):
        self.tools.cancelled = lambda: True
        with self.assertRaises(Declined):
            self.tools.execute('write_file', {'path': 'should-not-exist.txt', 'content': 'x', 'expected_sha256': ''})
        self.assertFalse((self.root / 'should-not-exist.txt').exists())

    def test_fill_cannot_use_a_later_button_result_as_its_condition(self):
        with self.assertRaisesRegex(ValueError, '尚未发送'):
            self.tools.execute('browser_action', {'kind': 'fill', 'value': '25',
                'condition': {'property': 'text', 'expected': '77'}})
        self.assertIsNone(self.tools._service)

    def test_unknown_preview_locks_same_project_but_not_other_projects(self):
        self.tools._preview = Mock()
        self.tools._preview.owns.return_value = True
        browser = Mock()
        browser.call.side_effect = lambda op, **args: ({'url': 'http://127.0.0.1:12345/owned', 'element': {'tag': 'input'}}
            if op == 'prepare' else {'outcome': 'unknown', 'execution_status': 'sent'})
        self.tools.browser_factory = lambda: browser
        args = {'tab_id': 'tab', 'snapshot_id': 'snapshot', 'element_id': '1', 'kind': 'fill',
                'value': '25', 'condition': {'property': 'value', 'expected': '25'}}
        self.assertEqual(self.tools.execute('browser_action', args)['outcome'], 'unknown')
        again = SimonWorkTools(self.root, self.tools.directory, 'next', self.review,
                              conversation_id='conversation', allow_web=False)
        self.addCleanup(again.close)
        again._preview = self.tools._preview
        again.browser_factory = lambda: browser
        self.assertEqual(again.execute('browser_action', args)['reason'], 'unresolved_browser_action_no_retry')
        second_root = self.root.parent / 'independent'; second_root.mkdir()
        other = SimonWorkTools(second_root, self.tools.directory, 'other', self.review,
                              conversation_id='other', allow_web=False)
        self.addCleanup(other.close)
        other._preview = self.tools._preview
        other.browser_factory = lambda: browser
        browser.call.side_effect = lambda op, **args: ({'url': 'http://127.0.0.1:12345/owned', 'element': {'tag': 'input'}}
            if op == 'prepare' else {'outcome': 'success', 'execution_status': 'sent', 'samples': 3})
        self.assertEqual(other.execute('browser_action', args)['outcome'], 'success')
        self.assertEqual(sum(call.args[0] == 'action' for call in browser.call.call_args_list), 2)

    def test_completion_does_not_accept_unrelated_command_or_zero_tests(self):
        task = '创建项目并运行单元测试，browser_read 保存截图'
        steps = [{'tool': 'run_command', 'arguments': {'command': 'Write-Output done'},
                  'result': {'outcome': 'success', 'output': 'done'}}]
        self.assertIn('测试', self.tools.completion_problem(task, steps))
        steps.append({'tool': 'run_command', 'arguments': {'command': 'python -m unittest'},
                      'result': {'outcome': 'success', 'output': 'Ran 0 tests in 0s\nOK'}})
        self.assertIn('没有发现', self.tools.completion_problem(task, steps))
        steps[-1]['result']['output'] = 'Ran 5 tests in 0.1s\nOK'
        self.assertIn('浏览器', self.tools.completion_problem(task, steps))
        steps.append({'tool': 'browser_read', 'result': {'outcome': 'success'}})
        self.assertIsNone(self.tools.completion_problem(task, steps))

    def test_false_completion_gets_one_repair_chance_then_incomplete(self):
        client = Mock(model='fixture')
        client.request.return_value = {'message': {'content': '已经创建文件并运行测试。'}}
        result = run_work(client, '创建项目并运行单元测试', [], self.tools,
                          lambda: False, lambda e: None, lambda text: None)
        self.assertEqual(result['outcome'], 'incomplete')
        self.assertEqual(client.request.call_count, 2)
        self.assertEqual(result['steps'], [])

    def test_truncated_generation_is_discarded_before_single_recovery(self):
        client = Mock(model='fixture')
        client.request.side_effect = [
            {'done_reason': 'length', 'message': {'tool_calls': [{'function': {'name': 'write_file', 'arguments': {'path': 'bad.txt', 'content': 'bad', 'expected_sha256': ''}}}]}},
            {'message': {'content': '无法继续，未创建文件。'}}]
        result = run_work(client, '说明情况', [], self.tools, lambda: False, lambda e: None, lambda text: None)
        self.assertFalse((self.root / 'bad.txt').exists())
        self.assertEqual(result['steps'], [])
        self.assertEqual(client.request.call_count, 2)


if __name__ == '__main__': unittest.main()
