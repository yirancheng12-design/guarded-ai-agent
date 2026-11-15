from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from guarded_agent import harness_runtime as runtime


class HarnessRuntimeTests(unittest.TestCase):
    def setUp(self):
        # The UI reuse path warms inference in a background thread. Keep unit
        # tests isolated from the installed model and its process state.
        warmer = patch.object(runtime, 'warm_model')
        self.warm = warmer.start()
        self.addCleanup(warmer.stop)
    def test_log_reader_uses_latest_known_loopback_url(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / 'runtime.log'
            log.write_text('http://evil.example/?token=leak\nhttp://127.0.0.1:8775/?token=first\nhttp://127.0.0.1:8775/?token=latest\n')
            with patch.object(runtime, 'LOG', log):
                self.assertEqual(runtime.launch_url(), 'http://127.0.0.1:8775/?token=latest')

    def test_probe_rejects_non_loopback_origin(self):
        with patch.object(runtime.urllib.request, 'build_opener') as opener:
            self.assertFalse(runtime.ready('https://evil.example/?token=secret'))
            opener.assert_not_called()

    def test_open_reuses_running_runtime(self):
        with patch.object(runtime.Path, 'is_file', return_value=True), patch.object(runtime, 'launch_url', return_value=runtime.ORIGIN + '/?token=existing'), patch.object(runtime, 'ready', return_value=True), patch.object(runtime.subprocess, 'run') as launch:
            self.assertEqual(runtime.open_workbench()['origin'], runtime.ORIGIN)
            launch.assert_not_called()

    def test_open_waits_for_existing_starting_process(self):
        with patch.object(runtime, 'launch_url', return_value=runtime.ORIGIN + '/?token=starting'), patch.object(runtime, 'ready', side_effect=[False, False, True]), patch.object(runtime, 'managed_process_running', return_value=True), patch.object(runtime.Path, 'is_file', return_value=True), patch.object(runtime.subprocess, 'run') as launch:
            self.assertEqual(runtime.open_workbench()['origin'], runtime.ORIGIN)
            launch.assert_not_called()

    def test_open_starts_missing_process_once(self):
        with patch.object(runtime, 'launch_url', return_value=runtime.ORIGIN + '/?token=new'), patch.object(runtime, 'ready', side_effect=[False, False, True]), patch.object(runtime, 'managed_process_running', return_value=False), patch.object(runtime.Path, 'is_file', return_value=True), patch.object(runtime.subprocess, 'run') as launch:
            launch.return_value.returncode = 0
            self.assertEqual(runtime.open_workbench()['origin'], runtime.ORIGIN)
            launch.assert_called_once()
