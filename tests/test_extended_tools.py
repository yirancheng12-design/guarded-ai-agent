from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.util import find_spec
from unittest.mock import patch
import json
import tempfile
import threading
import unittest
from guarded_agent.browser_tools import BrowserWorker, browser_url
from guarded_agent.work_tools import WorkTools
from guarded_agent.work_agent import run_work
from tests.test_work_agent import Client, call


class ExtendedToolsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'workspace'; self.root.mkdir()
        self.data = Path(self.temp.name) / 'data'
        self.tools = WorkTools(self.root, self.data, 'test', lambda *a: True, conversation_id='conversation')

    def test_search_and_precise_edit_preserve_unrelated_lines(self):
        self.tools.write_file('src/math.py', 'def add(a,b):\n    return a-b\n# keep this\n', '')
        matches = self.tools.search_files('return', '*.py')['matches']
        self.assertEqual(matches[0]['line'], 2)
        read = self.tools.read_file('src/math.py')
        result = self.tools.edit_file('src/math.py', 'return a-b', 'return a+b', read['sha256'])
        self.assertEqual((self.root / 'src/math.py').read_text(), 'def add(a,b):\n    return a+b\n# keep this\n')
        self.assertIn('return a-b', Path(result['backup']).read_text())

    def test_ambiguous_edit_does_not_write(self):
        self.tools.write_file('a.txt', 'same same', '')
        with self.assertRaises(ValueError): self.tools.edit_file('a.txt', 'same', 'new', self.tools.read_file('a.txt')['sha256'])
        self.assertEqual((self.root / 'a.txt').read_text(), 'same same')

    def test_precise_edit_maps_logical_lines_to_windows_crlf(self):
        (self.root / 'windows.py').write_bytes(b'def f():\r\n    return 1\r\n# keep\r\n')
        read = self.tools.read_file('windows.py')
        self.tools.edit_file('windows.py', 'def f():\n    return 1', 'def f():\n    return 2', read['sha256'])
        self.assertEqual((self.root / 'windows.py').read_bytes(), b'def f():\r\n    return 2\r\n# keep\r\n')

    def test_recursive_search_does_not_return_credentials(self):
        (self.root / '.env').write_text('secret marker')
        (self.root / 'safe.txt').write_text('normal marker')
        self.assertEqual([m['path'] for m in self.tools.search_files('marker')['matches']], ['safe.txt'])

    def test_unknown_task_checkpoint_survives_restart_without_replay(self):
        client = Client([call('run_command', command='fixture')])
        with patch.object(self.tools, 'execute', return_value={'outcome': 'unknown', 'reason': 'sent_timeout'}):
            run_work(client, 'test', [], self.tools, lambda: False, lambda _: None, lambda _: None)
        again = WorkTools(self.root, self.data, 'new', lambda *a: False, conversation_id='conversation')
        self.assertEqual(again.task_status()['previous']['outcome'], 'unknown')
        self.assertEqual(again.task_status()['previous']['steps'][0]['result']['reason'], 'sent_timeout')

    def test_incomplete_plan_is_not_marked_completed(self):
        client = Client([call('update_plan', steps=[{'title': 'work remains', 'status': 'pending'}]), {'content': 'finished'}])
        result = run_work(client, 'test', [], self.tools, lambda: False, lambda _: None, lambda _: None)
        self.assertEqual(result['outcome'], 'incomplete')

    def test_final_plan_reconciliation_does_not_repeat_completed_action(self):
        client = Client([call('update_plan', steps=[{'title': 'save file', 'status': 'pending'}]),
                         call('write_file', path='done.txt', content='done', expected_sha256=''),
                         {'content':'file saved'},
                         call('update_plan', steps=[{'title': 'save file', 'status': 'completed'}]),
                         {'content':'done and checked'}])
        result = run_work(client, 'save file', [], self.tools, lambda: False, lambda _: None, lambda _: None)
        self.assertEqual(result['outcome'], 'completed')
        self.assertEqual(sum(s['tool']=='write_file' for s in result['steps']), 1)

    @unittest.skipUnless(all(find_spec(name) for name in ('docx', 'openpyxl', 'pptx')), 'Install the optional Office document packages')
    def test_office_documents_are_reopened_and_content_verified(self):
        for extension, kwargs, text in [
            ('docx', {'paragraphs': ['中文验收内容']}, '中文验收内容'),
            ('xlsx', {'rows': [['品类','数量'], ['苹果',3], ['梨',2]]}, '苹果\t3'),
            ('pptx', {'slides': [{'title':'验收标题','bullets':['第一项','第二项']}]}, '第二项')]:
            with self.subTest(extension=extension):
                result = self.tools.create_document('test.' + extension, '标题', **kwargs)
                self.assertEqual(result['outcome'], 'success')
                self.assertIn(text, self.tools.read_document('test.' + extension)['text'])
                with self.assertRaises(ValueError): self.tools.create_document('test.' + extension, 'overwrite', **kwargs)

    @unittest.skipUnless(find_spec('pypdf'), 'Install pypdf to test PDF reading')
    def test_pdf_read(self):
        from pypdf import PdfWriter
        writer = PdfWriter(); writer.add_blank_page(200,200); writer.write(self.root / 'blank.pdf')
        self.assertIn('第 1 页', self.tools.read_document('blank.pdf')['text'])

    @unittest.skipUnless(find_spec('PIL'), 'Install Pillow to test image processing')
    def test_image_is_sent_to_local_model_as_actual_pixels(self):
        from PIL import Image
        Image.new('RGB',(120,80),'red').save(self.root / 'red.png')
        client = Client([{'content':'红色矩形'}]); self.tools.vision_client = client
        with patch.object(client, 'request', wraps=client.request) as request:
            result = self.tools.describe_image('red.png','描述颜色')
        payload = request.call_args.args[1]
        self.assertGreater(len(payload['messages'][1]['images'][0]), 100)
        self.assertEqual(result['analysis'], '红色矩形')

    def test_browser_url_cannot_open_files_or_assistant_control_endpoint(self):
        for url in ['file:///C:/secret', 'javascript:alert(1)', 'http://127.0.0.1:8765', 'http://user:password@example.com']:
            with self.subTest(url=url), self.assertRaises(ValueError): browser_url(url)

    def test_unknown_browser_action_blocks_new_run(self):
        class FakeBrowser:
            def call(self, name, **kwargs): return {'url':'https://example.com','element':{}} if name == 'prepare' else {'outcome':'unknown','execution_status':'sent'}
        self.tools.browser_factory = FakeBrowser
        args = dict(tab_id='tab',snapshot_id='snap',element_id='1',kind='click',value='',condition={'property':'text','expected':'done'})
        self.assertEqual(self.tools.browser_action(**args)['outcome'], 'unknown')
        again = WorkTools(self.root, self.data, 'next', lambda *a: True, browser_factory=lambda: self.fail('must not call browser'))
        self.assertEqual(again.browser_action(**args)['reason'], 'unresolved_browser_action_no_retry')


PAGE = '''<!doctype html><html><body><h1>Browser acceptance</h1>
<label>Name <input id="name"></label><input type="password" value="not-for-model">
<button onclick="window.count=(window.count||0)+1;document.getElementById('result').textContent='Saved '+document.getElementById('name').value+' '+window.count">Save</button>
<p id="result">Not saved</p></body></html>'''


class FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        raw=PAGE.encode(); self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8')
        self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def log_message(self,*args): pass


class BrowserIntegrationTests(unittest.TestCase):
    def test_real_chromium_fill_click_postcondition_and_stale_snapshot(self):
        if find_spec('playwright') is None:
            self.skipTest('Install Playwright and Chromium to run browser integration tests')
        server=ThreadingHTTPServer(('127.0.0.1',0),FixtureHandler)
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        try:
            with tempfile.TemporaryDirectory() as folder:
                browser=BrowserWorker(Path(folder)/'profile',headed=False)
                try:
                    snapshot=browser.open(f'http://127.0.0.1:{server.server_port}/')
                    self.assertEqual(snapshot['outcome'],'success')
                    self.assertNotIn('not-for-model',json.dumps(snapshot))
                    input_id=next(e['id'] for e in snapshot['elements'] if e['tag']=='input')
                    result=browser.action(snapshot['tab_id'],snapshot['snapshot_id'],input_id,'fill','Guarded',{'property':'value','expected':'Guarded'})
                    self.assertEqual(result['samples'],3)
                    snapshot=browser.snapshot(snapshot['tab_id'])
                    button_id=next(e['id'] for e in snapshot['elements'] if e['tag']=='button')
                    result=browser.action(snapshot['tab_id'],snapshot['snapshot_id'],button_id,'click','',{'property':'text','expected':'Saved Guarded 1'})
                    self.assertEqual(result['outcome'],'success')
                    stale=browser.action(snapshot['tab_id'],snapshot['snapshot_id'],button_id,'click','',{'property':'text','expected':'Saved Guarded 2'})
                    self.assertEqual(stale['execution_status'],'not_sent')
                    self.assertIn('Saved Guarded 1',browser.snapshot(snapshot['tab_id'])['text'])
                finally: browser.close()
        finally: server.shutdown(); server.server_close(); thread.join(2)
