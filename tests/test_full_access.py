from pathlib import Path
from unittest.mock import Mock, patch
import tempfile
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen

from guarded_agent.simon_work import SimonWorkTools
from guarded_agent.work_tools import Declined
from guarded_agent.work_preview import WorkspacePreview


class FullAccessTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.root = self.base / 'work'; self.root.mkdir()
        self.outside = self.base / 'outside'; self.outside.mkdir()
        self.approve = Mock(return_value=False)
        self.tools = SimonWorkTools(self.root, self.base / 'data', 'fulltest', self.approve, full_access=True)
        self.addCleanup(self.tools.close)

    def test_public_search_and_fetch_after_local_read_need_no_approval(self):
        self.tools.allow_web = True
        (self.root / 'notes.txt').write_text('local context')
        self.tools.read_file('notes.txt')
        with patch('guarded_agent.work_tools.public_get', return_value={
                'text': '<rss><channel><item><title>Result</title><link>https://example.com</link></item></channel></rss>'}):
            self.assertEqual(self.tools.search_web('Python documentation')['outcome'], 'success')
        with patch('guarded_agent.work_tools.public_get', return_value={
                'text': 'page content', 'content_type': 'text/plain'}):
            self.assertEqual(self.tools.fetch_url('https://example.com/search?q=python#results')['text'], 'page content')
        self.approve.assert_not_called()

    def test_public_browser_url_with_query_needs_no_approval(self):
        self.tools.allow_web = True
        self.tools.review('打开浏览器页面', {'url': 'https://example.com/search?q=python#results'})
        self.approve.assert_not_called()

    def test_embedded_url_credentials_still_need_approval(self):
        self.tools.allow_web = True
        for title in ('打开浏览器页面', '核对联网内容'):
            with self.subTest(title=title), self.assertRaises(Declined):
                self.tools.review(title, {'url': 'https://user:password@example.com/page?q=x'})

    def test_manual_mode_still_reviews_network(self):
        self.tools.auto_execute = False
        self.tools.local_context = True
        with patch('guarded_agent.work_tools.public_get') as get:
            with self.assertRaises(Declined):
                self.tools.search_web('Python documentation')
            get.assert_not_called()

    def test_cancelled_query_does_not_connect(self):
        self.tools.local_context = True
        self.tools.cancelled = lambda: True
        with patch('guarded_agent.work_tools.public_get') as get:
            with self.assertRaises(Declined):
                self.tools.search_web('Python documentation')
            get.assert_not_called()

    def test_external_read_edit_backup_and_search(self):
        file = self.outside / 'example.txt'; file.write_text('original')
        self.assertIn('example.txt', [x['name'] for x in self.tools.execute('list_files', {'path': str(self.outside)})['items']])
        self.assertEqual(self.tools.execute('search_files', {'path': str(self.outside), 'query': 'original'})['matches'][0]['path'], str(file))
        read = self.tools.execute('read_file', {'path': str(file)})
        result = self.tools.execute('edit_file', {'path': str(file), 'old_text': 'original', 'new_text': 'changed', 'expected_sha256': read['edit_version']})
        self.assertEqual(file.read_text(), 'changed')
        self.assertEqual(Path(result['backup']).read_text(), 'original')
        self.assertEqual(result['outcome'], 'success')
        self.approve.assert_not_called()

    def test_default_remains_scoped_and_model_cannot_enable_access(self):
        scoped = SimonWorkTools(self.root, self.base / 'data', 'scoped', self.approve)
        self.addCleanup(scoped.close)
        with self.assertRaises(ValueError): scoped.execute('list_files', {'path': str(self.outside)})
        with self.assertRaises(ValueError): scoped.execute('list_files', {'path': '.', 'full_access': True})
        self.assertFalse(scoped.full_access)

    def test_credentials_network_and_device_paths_remain_protected(self):
        for value in [str(self.outside / '.env'), str(self.outside / '.ssh/id_rsa'),
                      str(self.outside / 'secret.pem'), '//server/share/file',
                      '\\\\?\\C:\\file', str(self.outside / 'a.txt') + ':stream']:
            with self.subTest(value=value), self.assertRaises(ValueError): self.tools.path(value)

    def test_real_routine_command_outside_workspace_is_automatic(self):
        (self.outside / 'visible.txt').write_text('test')
        command = f"Get-ChildItem -LiteralPath '{self.outside}' | Select-Object -ExpandProperty Name"
        result = self.tools.execute('run_command', {'command': command})
        self.assertEqual(result['outcome'], 'success')
        self.assertIn('visible.txt', result['output'])
        self.approve.assert_not_called()

    def test_sensitive_commands_still_stop_before_execution(self):
        for command in ('Remove-Item -Recurse D:/important', 'Set-MpPreference -DisableRealtimeMonitoring $true',
                        'git push', 'Start-Process tool.exe -Verb RunAs'):
            with self.subTest(command=command), patch.object(self.tools, '_run_approved_command') as execute:
                with self.assertRaises(Declined): self.tools.execute('run_command', {'command': command})
                execute.assert_not_called()

    def test_full_access_does_not_expand_http_preview_root(self):
        (self.outside / 'external.html').write_text('<h1>outside</h1>')
        with self.assertRaisesRegex(ValueError, '仅服务工作区'):
            self.tools.execute('preview_file', {'path': str(self.outside / 'external.html')})
        with patch.object(self.tools, '_browser_service') as browser:
            preview = WorkspacePreview(self.tools)
            try:
                with self.assertRaises(HTTPError) as caught:
                    urlopen(preview.url('../outside/external.html'))
                caught.exception.close()
            finally: preview.close()

    def test_regular_button_expanded_but_send_stays_reviewed(self):
        details = {'action': {'kind': 'invoke'}, 'target_element': {'control_type': 50000, 'name': '开始计算'}}
        self.tools.review('执行桌面动作', details)
        self.approve.assert_not_called()
        details['target_element']['name'] = '发送消息'
        with self.assertRaises(Declined): self.tools.review('执行桌面动作', details)


if __name__ == '__main__': unittest.main()
