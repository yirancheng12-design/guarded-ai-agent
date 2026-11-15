from pathlib import Path
import http.client
import base64
import json
import tempfile
import threading
import unittest
from unittest.mock import patch
from guarded_agent.web import make_server, serve


class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.server = make_server(self.temp.name, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(2); self.temp.cleanup()

    def request(self, path, data=None, headers=None, token=True):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        h = {'X-Guarded-Token': self.server.token} if token else {}
        if data is not None: h['Content-Type'] = 'application/json'
        h.update(headers or {})
        connection.request('GET' if data is None else 'POST', path, None if data is None else json.dumps(data), h)
        response = connection.getresponse()
        status, body, response_headers = response.status, response.read(), dict(response.getheaders())
        connection.close()
        return status, body, response_headers

    def test_index_has_local_assets_and_session_token(self):
        status, body, headers = self.request('/', token=False)
        self.assertEqual(status, 200)
        self.assertIn(self.server.token.encode(), body)
        self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])

    def test_private_api_requires_token(self):
        self.assertEqual(self.request('/api/state', token=False)[0], 403)

    def test_evolution_settings_are_authenticated_and_validated(self):
        self.assertEqual(self.request('/api/evolution', token=False)[0],403)
        self.assertEqual(self.request('/api/evolution/settings',{'learning':True,'repair':True},token=False)[0],403)
        status,body,_=self.request('/api/evolution/settings',{'learning':True,'repair':False})
        self.assertEqual(status,200); self.assertTrue(json.loads(body)['learning'])
        self.assertFalse(json.loads(body)['repair'])
        self.assertEqual(self.request('/api/evolution/settings',{'learning':'yes','repair':True})[0],400)
        self.assertEqual(self.request('/api/evolution/check',{'model':'local-model'})[0],400)

    def test_harness_status_requires_session_token(self):
        self.assertEqual(self.request('/api/harness', token=False)[0], 403)

    def test_harness_open_returns_authenticated_loopback_url(self):
        result = {'url': 'http://127.0.0.1:8775/?token=test-only', 'origin': 'http://127.0.0.1:8775'}
        with patch('guarded_agent.harness_runtime.open_workbench', return_value=result) as launch:
            status, body, _ = self.request('/api/harness/open', {})
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body), result)
            launch.assert_called_once_with()

    def test_harness_open_cross_origin_does_not_start_process(self):
        with patch('guarded_agent.harness_runtime.open_workbench') as launch:
            self.assertEqual(self.request('/api/harness/open', {}, {'Origin': 'http://evil.example'})[0], 403)
            launch.assert_not_called()

    def test_harness_frame_policy_is_limited_to_loopback_runtime(self):
        _, _, headers = self.request('/')
        self.assertIn("frame-src 'self' http://127.0.0.1:8775;", headers['Content-Security-Policy'])

    def test_cross_origin_posts_are_rejected_even_with_token(self):
        self.assertEqual(self.request('/api/conversations', {}, {'Origin': 'http://evil.example'})[0], 403)

    def test_rebinding_host_is_rejected(self):
        self.assertEqual(self.request('/', headers={'Host':'evil.example'})[0], 403)

    def test_conversation_round_trip(self):
        status, raw, _ = self.request('/api/conversations', {})
        self.assertEqual(status, 200)
        identifier = json.loads(raw)['id']
        status, raw, _ = self.request('/api/state?conversation=' + identifier)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)['messages'], [])

    def test_chat_body_does_not_create_arbitrary_desktop_action(self):
        identifier = self.server.controller.store.create()
        status, _, _ = self.request('/api/send', {'conversation':identifier, 'text':'click', 'model':'test', 'mode':'desktop'})
        self.assertEqual(status, 400)
        self.assertFalse((Path(self.temp.name) / 'actions.sqlite3').exists())

    def test_path_traversal_does_not_read_files(self):
        self.assertEqual(self.request('/../chat.sqlite3')[0], 404)

    def test_boot_suite_assets_are_served(self):
        for path, mime in [('/boot-suite/lib/splash.js', 'text/javascript'),
                           ('/boot-suite/templates/archive-warm/template.json', 'application/json'),
                           ('/dsh-startup/asset/guarded-icon.png', 'image/png')]:
            with self.subTest(path=path):
                status, raw, headers = self.request(path, token=False)
                self.assertEqual(status, 200)
                self.assertTrue(raw)
                self.assertEqual(headers['Content-Type'], mime)

    def test_boot_suite_routes_cannot_escape_vendor_directory(self):
        for path in ['/boot-suite/../../web.py', '/boot-suite/%2e%2e/%2e%2e/web.py',
                     '/dsh-startup/template-asset/archive-warm/../../../../web.py',
                     '/dsh-startup/asset/../../../web.py', '/boot-suite/SOURCE.json/extra']:
            with self.subTest(path=path):
                self.assertEqual(self.request(path, token=False)[0], 404)

    def test_boot_suite_cannot_be_read_through_rebinding_host(self):
        self.assertEqual(self.request('/boot-suite/lib/splash.js', headers={'Host': 'evil.example'}, token=False)[0], 403)

    def test_wrong_content_type_rejected(self):
        self.assertEqual(self.request('/api/conversations', {}, {'Content-Type':'text/plain'})[0], 400)

    def test_second_server_cannot_bind_or_recover_active_messages(self):
        identifier = self.server.controller.store.create()
        self.server.controller.store.begin(identifier, 'pending task', 'work', 'pending-id')
        with patch('guarded_agent.web.Controller') as controller:
            with self.assertRaises(OSError): make_server(self.temp.name, self.server.server_port)
            controller.assert_not_called()
        self.assertEqual(self.server.controller.store.messages(identifier)[-1]['status'], 'running')

    def test_second_launcher_opens_existing_server_and_returns(self):
        with patch('guarded_agent.web.Controller') as controller, patch('guarded_agent.web.webbrowser.open') as open_browser:
            serve(self.server.server_port, self.temp.name, open_browser=True)
            open_browser.assert_called_once_with(f'http://127.0.0.1:{self.server.server_port}')
            controller.assert_not_called()

    def test_uploaded_file_is_saved_inside_workspace_without_overwriting(self):
        data = {'name': 'example.txt', 'data': base64.b64encode('中文附件'.encode()).decode()}
        status, raw, _ = self.request('/api/import-file', data)
        self.assertEqual(status, 200)
        first = json.loads(raw)
        root = Path(self.server.controller.store.settings()['workspace'])
        self.assertEqual((root / first['path']).read_text(encoding='utf-8'), '中文附件')
        second = json.loads(self.request('/api/import-file', data)[1])
        self.assertNotEqual(first['path'], second['path'])

    def test_upload_rejects_traversal_and_executables(self):
        for name in ('../escape.txt', 'C:\\escape.txt', 'run.exe', '.env'):
            with self.subTest(name=name):
                self.assertEqual(self.request('/api/import-file', {'name': name, 'data': 'eA=='})[0], 400)

    def test_workspace_binding_and_preview_are_conversation_scoped(self):
        first=Path(self.temp.name)/'first'; second=Path(self.temp.name)/'second'
        status,raw,_=self.request('/api/conversations',{'workspace':str(first)})
        self.assertEqual(status,200); conversation=json.loads(raw)['id']
        (first/'same.txt').write_text('first project')
        self.request('/api/settings',{'workspace':str(second)})
        (second/'same.txt').write_text('second project')
        status,raw,_=self.request('/api/file-preview',{'conversation':conversation,'path':'same.txt'})
        self.assertEqual(status,200); self.assertEqual(json.loads(raw)['text'],'first project')
        self.assertEqual(self.request('/api/file-preview',{'conversation':conversation,'path':'../outside.txt'})[0],400)
        self.assertEqual(self.request('/api/file-preview',{'conversation':conversation,'path':'same.txt'},token=False)[0],403)

    def test_change_preview_and_restore_validate_ownership_and_hash(self):
        from hashlib import sha256
        controller=self.server.controller; conversation=controller.store.create()
        controller.store.begin(conversation,'change','work','fixture-run')
        tools=controller.file_tools(conversation,'fixture-run')
        (tools.root/'note.txt').write_bytes(b'old')
        written=tools.write_file('note.txt','new',sha256(b'old').hexdigest())
        controller.store.finish('fixture-run','done','completed',{})
        data={'conversation':conversation,'change_id':written['change_id']}
        self.assertEqual(self.request('/api/change-preview',data)[0],200)
        self.assertEqual(self.request('/api/restore-change',{**data,'expected_sha256':'stale'})[0],400)
        other=controller.store.create()
        self.assertEqual(self.request('/api/change-preview',{**data,'conversation':other})[0],400)
        self.assertEqual((tools.root/'note.txt').read_bytes(),b'new')
        status,raw,_=self.request('/api/restore-change',{**data,'expected_sha256':written['sha256']})
        self.assertEqual(status,200); self.assertEqual(json.loads(raw)['outcome'],'completed')
        self.assertEqual((tools.root/'note.txt').read_bytes(),b'old')
