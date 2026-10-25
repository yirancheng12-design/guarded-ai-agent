from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from guarded_agent.github_tools import repository_id
from guarded_agent.work_tools import WorkTools, Declined
from guarded_agent.simon_work import SimonWorkTools
from guarded_agent.work_agent import run_work


class GitHubWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'workspace'
        self.root.mkdir()
        self.folder = self.root / 'repo'
        self.folder.mkdir()
        self.git('init', '-b', 'guarded/test')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        (self.folder / 'README.md').write_text('original', encoding='utf-8')
        self.git('add', 'README.md')
        self.git('commit', '-m', 'fixture')
        self.git('remote', 'add', 'origin', 'https://github.com/example/repository.git')
        self.tools = WorkTools(self.root, Path(self.temp.name) / 'data', 'github-test', lambda *args: True)

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.folder), *args], stderr=subprocess.DEVNULL).decode().strip()

    def change_and_test(self):
        (self.folder / 'README.md').write_text('changed', encoding='utf-8')
        with patch.object(self.tools, 'run_command', return_value={'outcome': 'success', 'exit_code': 0, 'output': 'tests passed'}):
            return self.tools.github_run_tests('repo', 'python -m unittest')

    def publish(self):
        return self.tools.github_publish('repo', 'Fix fixture', 'Summary and validation', ['README.md'])

    def test_repository_input_is_not_a_command_or_credential_url(self):
        self.assertEqual(repository_id('https://github.com/example/repository.git'), 'example/repository')
        for value in ('../repo', 'https://evil.invalid/example/repo', 'https://token@github.com/example/repo',
                      'example/repo;whoami', 'example/repo?token=x', 'example/..'):
            with self.subTest(value=value), self.assertRaises(ValueError): repository_id(value)

    def test_common_agent_loop_exposes_tools_and_returns_real_repository_state(self):
        responses = iter([{'message': {'tool_calls': [{'function': {'name': 'github_status', 'arguments': {'path': 'repo'}}}]}},
                          {'message': {'content': '已读取仓库实际状态。'}}])
        class Client:
            model = 'fixture'
            def request(inner, endpoint, request):
                self.assertIn('github_publish', [tool['function']['name'] for tool in request['tools']])
                self.assertIn('GitHub 仓库任务', request['messages'][0]['content'])
                return next(responses)
        result = run_work(Client(), '检查仓库状态', [], self.tools, lambda: False, lambda _: None, lambda _: None)
        self.assertEqual(result['outcome'], 'completed')
        self.assertEqual(result['steps'][0]['result']['branch'], 'guarded/test')

    def test_unborn_repository_status_and_tests_include_staged_files(self):
        self.git('checkout', '--orphan', 'guarded/empty')
        status = self.tools.github_status('repo')
        self.assertTrue(status['empty_repository'])
        self.assertIsNone(status['head'])
        self.assertEqual(status['files'], ['README.md'])
        before = status['snapshot']
        (self.folder / 'README.md').write_text('updated')
        self.assertNotEqual(self.tools.github_status('repo')['snapshot'], before)
        with patch.object(self.tools, 'run_command', return_value={'outcome': 'success', 'exit_code': 0}):
            self.assertTrue(self.tools.github_run_tests('repo', 'test')['validation_current'])
        with patch.object(self.tools, '_github_api') as api, self.assertRaisesRegex(ValueError, '首次提交'):
            self.publish()
        api.assert_not_called()
        self.assertFalse((self.tools.directory / 'github-actions.sqlite3').exists())

    def test_prepare_empty_clone_returns_usable_status(self):
        def clone(args, **kwargs):
            destination = args[-1]
            subprocess.check_call(['git', 'init', destination], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.check_call(['git', '-C', destination, 'remote', 'add', 'origin',
                                   'https://github.com/example/repository.git'])
            return subprocess.CompletedProcess(args, 0, '', '')
        process = self.tools._github_process
        def dispatch(args, **kwargs):
            return clone(args, **kwargs) if args[0] == 'clone' else process(args, **kwargs)
        with patch.object(self.tools, '_github_api', return_value={'default_branch': 'main'}), \
             patch.object(self.tools, '_github_process', side_effect=dispatch):
            status = self.tools.github_prepare('example/repository', 'empty-clone')
        self.assertEqual(status['outcome'], 'success')
        self.assertTrue(status['empty_repository'])
        self.assertTrue(status['branch'].startswith('guarded/'))
        self.assertEqual(status['files'], [])

    def test_status_reads_actual_identity_and_relative_changes(self):
        (self.folder / 'new.txt').write_text('new')
        status = self.tools.execute('github_status', {'path': 'repo'})
        self.assertEqual(status['repository'], 'example/repository')
        self.assertEqual(status['files'], ['new.txt'])
        self.assertTrue(status['publish_branch'])
        self.assertEqual(status['head'], self.git('rev-parse', 'HEAD'))

    def test_workspace_escape_is_rejected_even_for_full_access(self):
        tools = SimonWorkTools(self.root, self.tools.directory, 'full', lambda *args: True, full_access=True)
        self.addCleanup(tools.close)
        with self.assertRaises(ValueError): tools.github_status('../outside')
        self.assertIn('github_publish', tools.allowed_names)

    def test_prepare_never_overwrites_existing_directory(self):
        with patch.object(self.tools, '_github_api') as api, self.assertRaises(ValueError):
            self.tools.github_prepare('example/repository', 'repo')
        api.assert_not_called()

    def test_main_branch_cannot_be_published(self):
        self.git('branch', '-m', 'main')
        with self.assertRaisesRegex(ValueError, '独立分支'): self.publish()

    def test_publish_requires_host_recorded_tests(self):
        (self.folder / 'README.md').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'github_run_tests'): self.publish()

    def test_changed_files_invalidate_successful_tests(self):
        self.assertTrue(self.change_and_test()['validation_current'])
        (self.folder / 'README.md').write_text('changed again')
        with self.assertRaisesRegex(ValueError, 'github_run_tests'): self.publish()

    def test_test_command_that_changes_files_is_not_current_validation(self):
        def command(_):
            (self.folder / 'README.md').write_text('generated during test')
            return {'outcome': 'success', 'exit_code': 0}
        with patch.object(self.tools, 'run_command', side_effect=command):
            result = self.tools.github_run_tests('repo', 'python -m unittest')
        self.assertFalse(result['validation_current'])

    def test_credential_files_are_not_candidate_uploads(self):
        (self.folder / '.env').write_text('PRIVATE_SENTINEL')
        with self.assertRaises(ValueError): self.tools.github_status('repo')

    def test_declined_publication_does_not_commit_or_create_ledger(self):
        self.change_and_test()
        original = self.git('rev-parse', 'HEAD')
        self.tools.approve = lambda *args: False
        with patch.object(self.tools, '_github_api', return_value={'default_branch': 'main'}), self.assertRaises(Declined):
            self.publish()
        self.assertEqual(self.git('rev-parse', 'HEAD'), original)
        self.assertFalse((self.tools.directory / 'github-actions.sqlite3').exists())

    def test_unknown_push_is_not_replayed(self):
        self.change_and_test()
        git = self.tools._git
        pushes = []
        def dispatch(folder, *args):
            if args[0] == 'push':
                pushes.append(args)
                raise RuntimeError('connection lost')
            return git(folder, *args)
        def api(endpoint, data=None):
            return {'login': 'fixture', 'id': 123} if endpoint == '/user' else {'default_branch': 'main'}
        with patch.object(self.tools, '_git', side_effect=dispatch), patch.object(self.tools, '_github_api', side_effect=api):
            self.assertEqual(self.publish()['outcome'], 'unknown')
            # Fresh validation must still not bypass the durable pending record.
            with patch.object(self.tools, 'run_command', return_value={'outcome': 'success', 'exit_code': 0}):
                self.tools.github_run_tests('repo', 'python -m unittest')
            result = self.tools.github_publish('repo', 'Fix fixture', 'body', ['README.md'])
        self.assertEqual(len(pushes), 1)
        self.assertEqual(result['outcome'], 'unknown')

    def test_successful_pr_is_verified_after_push(self):
        self.change_and_test()
        git = self.tools._git
        def dispatch(folder, *args):
            return '' if args[0] == 'push' else git(folder, *args)
        endpoints = []
        def api(endpoint, data=None):
            endpoints.append(endpoint)
            if endpoint == '/user': return {'login': 'fixture', 'id': 123}
            if '/git/ref/heads/' in endpoint: return {'object': {'sha': self.git('rev-parse', 'HEAD')}}
            if endpoint.endswith('/pulls'): return {'number': 7}
            if endpoint.endswith('/pulls/7'):
                return {'head': {'sha': self.git('rev-parse', 'HEAD')}, 'html_url': 'https://github.com/example/repository/pull/7'}
            return {'default_branch': 'main'}
        with patch.object(self.tools, '_git', side_effect=dispatch), patch.object(self.tools, '_github_api', side_effect=api):
            result = self.publish()
        self.assertEqual(result['outcome'], 'success')
        self.assertFalse(result['merged'])
        self.assertTrue(endpoints[-1].endswith('/pulls/7'))
        self.assertIn('/pull/7', result['url'])
