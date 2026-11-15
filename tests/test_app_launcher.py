from contextlib import nullcontext
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import os
from dataclasses import asdict
from guarded_agent.app_launcher import AppLauncher, AppBackend, open_request
from guarded_agent.model import Rect, WindowIdentity
from guarded_agent.simulation import FakeClock


class Backend:
    def __init__(self, clock, visible=True, receipt='sent', frozen=False):
        self.clock, self.visible, self.receipt, self.frozen = clock, visible, receipt, frozen
        self.calls, self.sequence = 0, 0
        self.interactive = True

    def sample(self, path, timeout=3):
        self.sequence += 1
        return {'sequence': 1 if self.frozen else self.sequence, 'at': 1 if self.frozen else self.clock(),
                'interactive': self.interactive, 'reason': '' if self.interactive else 'session_locked',
                'window': {'identity': {'hwnd': 1, 'pid': 2, 'created': 3}, 'visible': self.visible, 'foreground': True}}

    def open(self, identifier, path, timeout=3):
        self.calls += 1
        return {'execution_status': self.receipt, 'reason': 'test_receipt'}


class AppLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.clock = FakeClock()
        self.backend = Backend(self.clock)
        self.discover = lambda: {'wechat': {'path': str(Path(self.temp.name) / 'Weixin.exe'), 'name': '微信'}}
        self.launcher = self.make_launcher()

    def make_launcher(self):
        return AppLauncher(self.temp.name, backend_factory=lambda: nullcontext(self.backend),
                           discover=self.discover, clock=self.clock, sleep=self.clock.sleep)

    def test_exact_open_requests(self):
        for text, expected in [('帮我打开微信', 'wechat'), ('打开wegame', 'wegame'), ('请打开 WeChat。', 'wechat'),
                               ('open WeGame', 'wegame'), ('打开计算器', 'calculator')]:
            self.assertEqual(open_request(text), expected)

    def test_explanations_and_negation_never_launch(self):
        for text in ['不要打开微信', '解释“打开微信”', '如何打开微信', '打开微信\n再发消息']:
            self.assertIsNone(open_request(text))

    def test_compound_or_shell_commands_are_not_reduced_to_open(self):
        for text in ['打开微信并发送消息', '打开wegame && calc', '打开C:\\anything.exe']:
            self.assertEqual(open_request(text), 'unsupported')

    def test_three_fresh_samples_verify_window(self):
        result = self.launcher.run('wechat', 'first')
        self.assertEqual(result['outcome'], 'success')
        self.assertEqual(self.backend.calls, 1)
        self.assertEqual(len(result['samples']), 3)

    def test_stale_samples_cannot_claim_success(self):
        self.backend.frozen = True
        self.assertEqual(self.launcher.run('wechat', 'first')['outcome'], 'unknown')
        self.assertEqual(self.backend.calls, 1)

    def test_unknown_blocks_launch_across_restart(self):
        self.backend.visible = False
        self.assertEqual(self.launcher.run('wechat', 'first')['outcome'], 'unknown')
        self.assertEqual(self.make_launcher().run('wechat', 'second')['outcome'], 'unknown')
        self.assertEqual(self.backend.calls, 1)

    def test_pending_can_only_resolve_with_independent_window_evidence(self):
        self.backend.visible = False
        self.launcher.run('wechat', 'first')
        self.backend.visible = True
        result = self.make_launcher().run('wechat', 'second')
        self.assertEqual(result['outcome'], 'success')
        self.assertEqual(result['execution_status'], 'not_sent')
        self.assertEqual(self.backend.calls, 1)

    def test_readonly_verification_never_launches_even_without_pending_action(self):
        result = self.launcher.run('wechat', 'read-only', verify_only=True)
        self.assertEqual((result['outcome'], result['execution_status']), ('success', 'not_sent'))
        self.assertEqual(self.backend.calls, 0)

    def test_readonly_verification_resolves_unknown_without_relaunch(self):
        self.backend.visible = False
        self.launcher.run('wechat', 'first')
        self.backend.visible = True
        result = self.launcher.run('wechat', 'check-only', verify_only=True)
        self.assertEqual(result['outcome'], 'success')
        self.assertEqual(result['execution_status'], 'not_sent')
        self.assertEqual(self.backend.calls, 1)

    def test_locked_session_sends_nothing(self):
        self.backend.interactive = False
        result = self.launcher.run('wechat', 'first')
        self.assertEqual((result['outcome'], result['execution_status']), ('failed', 'not_sent'))
        self.assertEqual(self.backend.calls, 0)

    def test_pre_cancel_sends_nothing(self):
        result = self.launcher.run('wechat', 'first', cancelled=lambda: True)
        self.assertEqual(result['outcome'], 'cancelled')
        self.assertEqual(self.backend.calls, 0)

    def test_unknown_app_sends_nothing(self):
        self.assertEqual(self.launcher.run('unsupported', 'first')['outcome'], 'unsupported')
        self.assertEqual(self.backend.calls, 0)

    def test_zero_size_helper_window_does_not_hide_real_window(self):
        native = AppBackend.__new__(AppBackend)
        native.sequence = 0
        native.api = MagicMock()
        native.api.interactive_state.return_value = {'interactive': True, 'reason': ''}
        native.api.windows.return_value = [(WindowIdentity(1, 2, 3), 'helper'), (WindowIdentity(4, 2, 3), 'main')]
        native.api.k.OpenProcess.return_value = 1
        def query(handle, flags, buffer, size): buffer.value = 'fake.exe'; return True
        native.api.k.QueryFullProcessImageNameW.side_effect = query
        native.api.rect.side_effect = [ValueError('zero size'), Rect(0, 0, 800, 600)]
        native.api.u.IsIconic.return_value = False
        native.api.foreground.return_value = 4
        with patch.object(native, '_matches', return_value=True):
            result = native.sample('fake.exe')
        self.assertEqual(result['window']['identity']['hwnd'], 4)

    def test_elevation_request_uses_normal_windows_launch_not_repeated_createprocess(self):
        native = AppBackend.__new__(AppBackend)
        native.api = MagicMock(); native.api.modifiers_down.return_value = False
        native.api.interactive_state.return_value = {'interactive': True}
        native.sample = lambda *a: {'interactive': True, 'window': None}
        error = OSError('requires elevation'); error.winerror = 740
        with patch('guarded_agent.app_launcher.installed_apps', return_value={'wegame': {'path': 'wegame.exe'}}), \
             patch('subprocess.Popen', side_effect=error) as launch, patch('os.startfile', create=True) as shell:
            result = native.open('wegame', 'wegame.exe')
            self.assertEqual(result['execution_status'], 'not_sent')
            self.assertEqual(result['reason'], 'requires_windows_permission')
            shell.assert_not_called()
            result = native.open_with_shell('wegame', 'wegame.exe')
        self.assertEqual(result['execution_status'], 'sent')
        launch.assert_called_once(); shell.assert_called_once()

    def hosted_backend(self):
        native = AppBackend.__new__(AppBackend)
        native.api = MagicMock()
        host = str(Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32' / 'ApplicationFrameHost.exe')
        child_image = str(Path(os.environ['ProgramFiles']) / 'WindowsApps' / 'Microsoft.WindowsCalculator_1' / 'CalculatorApp.exe')
        native._process_image = lambda pid: host if pid == 10 else child_image
        native.api.u.IsWindowVisible.return_value = True
        native.api.identity.return_value = WindowIdentity(20, 21, 22)
        native.api.u.EnumChildWindows.side_effect = lambda hwnd, callback, param: callback(20, param)
        return native

    @unittest.skipUnless(os.name == 'nt', 'Windows application frame hosting')
    def test_system_host_with_matching_child_is_recognized(self):
        native = self.hosted_backend()
        result = native._application_identity('C:/Windows/System32/calc.exe', WindowIdentity(1, 10, 11))
        self.assertEqual(result, asdict(WindowIdentity(20, 21, 22)))

    @unittest.skipUnless(os.name == 'nt', 'Windows application frame hosting')
    def test_same_named_non_system_host_is_not_trusted(self):
        native = self.hosted_backend()
        native._process_image = lambda pid: 'C:/untrusted/ApplicationFrameHost.exe'
        self.assertIsNone(native._application_identity('C:/Windows/System32/calc.exe', WindowIdentity(1, 10, 11)))
        native.api.u.EnumChildWindows.assert_not_called()

    @unittest.skipUnless(os.name == 'nt', 'Windows application frame hosting')
    def test_hidden_hosted_child_is_not_evidence(self):
        native = self.hosted_backend()
        native.api.u.IsWindowVisible.return_value = False
        self.assertIsNone(native._application_identity('C:/Windows/System32/calc.exe', WindowIdentity(1, 10, 11)))

    def test_hosted_app_switch_resets_consecutive_observations(self):
        original = self.backend.sample
        def sample(*args, **kwargs):
            result = original(*args, **kwargs)
            result['window']['application_identity'] = {'pid': result['sequence'] % 2}
            return result
        self.backend.sample = sample
        self.assertEqual(self.launcher.run('wechat', 'hosted-switch')['outcome'], 'unknown')
        self.assertEqual(self.backend.calls, 1)
