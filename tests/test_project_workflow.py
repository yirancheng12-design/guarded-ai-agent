from pathlib import Path
from hashlib import sha256
from unittest.mock import patch
from contextlib import closing
import base64
import json
import sqlite3
import tempfile
import threading
import time
import unittest
from guarded_agent.chat_service import Controller, Store, BusyError
from guarded_agent.work_tools import WorkTools, console_python


class ProjectWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.a = self.root/'alpha'; self.a.mkdir()
        self.b = self.root/'beta'; self.b.mkdir()
        self.controller = Controller(self.root/'data', projects_root=self.root/'library')
        self.conversation = self.controller.create_conversation(str(self.a))

    def save(self, content, expected='', path='note.txt'):
        run_id = __import__('uuid').uuid4().hex
        self.controller.store.begin(self.conversation, 'test save', 'work', run_id)
        tools = self.controller.file_tools(self.conversation, run_id)
        result = tools.write_file(path, content, expected)
        self.controller.store.finish(run_id, 'saved', 'completed', {'result':{'workspace':str(self.a),'steps':[{'tool':'write_file','result':result}]}})
        return result

    def test_switching_default_and_restart_preserve_conversation_workspace(self):
        self.controller.update_workspace(str(self.b))
        other = self.controller.create_conversation()
        self.assertEqual(self.controller.state(self.conversation)['settings']['workspace'], str(self.a))
        self.assertEqual(self.controller.state(other)['settings']['workspace'], str(self.b))
        recovered = Store(self.root/'data')
        self.assertEqual(recovered.settings(self.conversation)['workspace'], str(self.a))

    def test_agent_and_attachments_use_bound_workspace(self):
        self.controller.update_workspace(str(self.b))
        saved = self.controller.import_file('attached.txt', base64.b64encode(b'attachment').decode(), self.conversation)
        self.assertTrue((self.a/saved['path']).exists()); self.assertFalse((self.b/saved['path']).exists())
        roots=[]
        def fake_run(client, text, history, tools, *args):
            roots.append(tools.root); return {'outcome':'completed','text':'done'}
        with patch('guarded_agent.work_agent.run_work', side_effect=fake_run):
            self.controller.send(self.conversation,'work','fixture',mode='assistant')
            deadline=time.monotonic()+3
            while self.controller.state()['active']:
                if time.monotonic()>deadline: self.fail('Task did not finish')
                time.sleep(.01)
        self.assertEqual(roots,[self.a])

    def test_missing_bound_workspace_does_not_fall_back_to_other_project(self):
        self.controller.update_workspace(str(self.b)); self.a.rmdir()
        with self.assertRaisesRegex(ValueError,'不存在'):
            self.controller.file_tools(self.conversation)
        self.assertEqual(list(self.b.iterdir()),[])

    def test_legacy_database_backfills_workspace_from_its_actual_task(self):
        data=self.root/'legacy'; data.mkdir()
        with closing(sqlite3.connect(data/'chat.sqlite3')) as db:
            db.execute('CREATE TABLE conversations(id TEXT PRIMARY KEY,title TEXT,created REAL)')
            db.execute('CREATE TABLE messages(id TEXT PRIMARY KEY,conversation TEXT,role TEXT,content TEXT,mode TEXT,status TEXT,detail TEXT,created REAL)')
            db.execute("CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT)")
            db.execute('INSERT INTO conversations VALUES (?,?,?)',('old','old',1))
            db.execute('INSERT INTO settings VALUES (?,?)',('workspace',str(self.b)))
            db.execute('INSERT INTO messages VALUES (?,?,?,?,?,?,?,?)',('result','old','assistant','done','work','completed',json.dumps({'result':{'workspace':str(self.a)}}),1))
            db.commit()
        store=Store(data)
        self.assertEqual(store.settings('old')['workspace'],str(self.a))
        self.assertEqual(len(store.messages('old')),1)

    def test_project_library_finds_code_root_and_excludes_files(self):
        code=self.root/'library/01_AI/Example/outputs/app'; code.mkdir(parents=True)
        (code/'pyproject.toml').write_text('[project]')
        (self.root/'library/readme.txt').write_text('not a project')
        projects=self.controller.projects()
        self.assertEqual(len(projects),1)
        self.assertEqual(projects[0]['workspace'],str(code))

    def test_diff_and_reversible_restore_preserve_exact_bom_and_crlf(self):
        original=b'\xef\xbb\xbfline one\r\nline two\r\n'
        (self.a/'note.txt').write_bytes(original)
        result=self.save('new text\n',sha256(original).hexdigest())
        preview=self.controller.file_changes(self.conversation,result['change_id'])
        self.assertTrue(preview['can_restore']); self.assertIn('+new text',preview['diff'])
        restored=self.controller.restore_change(self.conversation,result['change_id'],preview['current_sha256'])
        self.assertEqual(restored['outcome'],'completed')
        self.assertEqual((self.a/'note.txt').read_bytes(),original)
        memory=self.controller.file_tools(self.conversation).task_status()['previous']
        self.assertEqual(memory['steps'][0]['tool'],'restore_file')
        self.assertEqual(memory['steps'][0]['result']['sha256'],sha256(original).hexdigest())
        change=restored['steps'][0]['result']['change_id']
        self.controller.restore_change(self.conversation,change,sha256(original).hexdigest())
        self.assertEqual((self.a/'note.txt').read_bytes(),b'new text\n')

    def test_external_changes_after_preview_are_not_overwritten(self):
        (self.a/'note.txt').write_text('old')
        result=self.save('new',sha256(b'old').hexdigest())
        preview=self.controller.file_changes(self.conversation,result['change_id'])
        (self.a/'note.txt').write_text('external change')
        with self.assertRaises(ValueError):
            self.controller.restore_change(self.conversation,result['change_id'],preview['current_sha256'])
        self.assertEqual((self.a/'note.txt').read_text(),'external change')

    def test_restore_checkpoint_failure_preserves_written_file_and_evidence(self):
        (self.a/'note.txt').write_bytes(b'old\r\n')
        saved=self.save('new',sha256(b'old\r\n').hexdigest())
        with patch.object(WorkTools, 'checkpoint', side_effect=OSError('disk full')):
            result=self.controller.restore_change(self.conversation,saved['change_id'],saved['sha256'])
        self.assertEqual(result['outcome'],'unknown')
        self.assertEqual((self.a/'note.txt').read_bytes(),b'old\r\n')
        self.assertEqual(result['steps'][0]['result']['sha256'],sha256(b'old\r\n').hexdigest())
        self.assertTrue(result['steps'][0]['result']['change_id'])
        self.assertEqual(self.controller.store.messages(self.conversation)[-1]['status'],'unknown')

    def test_new_files_are_previewable_but_never_deleted_by_restore(self):
        result=self.save('created')
        preview=self.controller.file_changes(self.conversation,result['change_id'])
        self.assertFalse(preview['can_restore'])
        with self.assertRaises(ValueError):
            self.controller.restore_change(self.conversation,result['change_id'],result['sha256'])
        self.assertTrue((self.a/'note.txt').exists())

    def test_another_conversation_cannot_read_or_restore_change(self):
        result=self.save('created'); other=self.controller.create_conversation(str(self.a))
        self.assertEqual(self.controller.file_changes(other)['changes'],[])
        with self.assertRaisesRegex(ValueError,'不属于'):
            self.controller.file_changes(other,result['change_id'])

    def test_corrupt_backup_cannot_restore(self):
        (self.a/'note.txt').write_text('old')
        result=self.save('new',sha256(b'old').hexdigest())
        Path(result['backup']).write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError,'校验失败'):
            self.controller.restore_change(self.conversation,result['change_id'],result['sha256'])
        self.assertEqual((self.a/'note.txt').read_text(),'new')

    def test_preview_blocks_path_escape_and_credentials(self):
        for path in ('../outside.txt','.env','file.key'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.controller.preview_file(self.conversation,path)

    def test_restore_cannot_race_running_task(self):
        result=self.save('created'); self.controller.active={'id':'other'}
        with self.assertRaises(BusyError):
            self.controller.restore_change(self.conversation,result['change_id'],result['sha256'])

    def test_change_log_failure_keeps_file_and_reports_unknown(self):
        tools=self.controller.file_tools(self.conversation)
        with patch('guarded_agent.file_changes.record_change',side_effect=OSError('disk full')):
            result=tools.write_file('saved.txt','saved','')
        self.assertEqual(result['outcome'],'unknown')
        self.assertEqual((self.a/'saved.txt').read_text(),'saved')

    def test_background_launcher_uses_console_interpreter_for_commands(self):
        console=self.root/'python.exe'; console.touch()
        with patch('sys.executable',str(self.root/'pythonw.exe')):
            self.assertEqual(console_python(),str(console))

    def test_no_silent_fallback_when_console_interpreter_missing(self):
        with patch('sys.executable',str(self.root/'pythonw.exe')):
            with self.assertRaises(RuntimeError): console_python()
