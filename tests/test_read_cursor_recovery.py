"""Recovery for actual model pagination errors, without replaying side effects."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import unittest

from guarded_agent.read_progress import normalize_read_arguments
from guarded_agent.work_agent import run_work
from guarded_agent.work_tools import WorkTools
from guarded_agent.simon_work import SimonWorkTools


def call(name, **args):
    return {'message': {'tool_calls': [{'function': {'name': name, 'arguments': args}}]}}


class ReadCursorRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/'workspace'
        self.root.mkdir()
        self.data = Path(self.temp.name)/'data'
        self.tools = WorkTools(self.root, self.data, 'cursor', lambda *args: False)

    def test_json_integer_strings_work_for_read_only_paging(self):
        (self.root/'a.txt').write_text('x'*7000)
        args = {'path':'a.txt','start_line':'1','line_count':'120','char_offset':'4000','max_chars':'4000'}
        result = self.tools.execute('read_file', args)
        self.assertEqual(result['char_offset'],4000)
        self.assertEqual(result['text'],'x'*3000)
        self.assertEqual(args['char_offset'],'4000')
        self.assertEqual(normalize_read_arguments('run_command',args),args)

    def test_ambiguous_and_out_of_range_values_still_fail_validation(self):
        (self.root/'a.txt').write_text('abc')
        for value in (True, 1.0, '1.0', '1e3', '-1', ' 1 ', '10000000000000000000000'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.tools.execute('read_file',{'path':'a.txt','char_offset':value})
        with self.assertRaises(ValueError):
            self.tools.execute('read_file',{'path':'a.txt','max_chars':'9999999'})

    def test_model_can_ignore_cursors_and_still_reach_unread_tail_then_write(self):
        source = 'a'*4000+'b'*4000+'TAIL_SENTINEL'
        (self.root/'a.js').write_text(source)
        outer = self
        class Client:
            model = 'fixture'
            count = 0
            def request(self,path,data):
                self.count += 1
                if self.count <= 7:
                    return call('read_file',path='a.js',start_line='1',char_offset='0',max_chars='4000')
                if self.count == 8:
                    last = next(json.loads(m['content']) for m in reversed(data['messages']) if m.get('role')=='tool')
                    outer.assertEqual(last['text'],'TAIL_SENTINEL')
                    outer.assertEqual(last['automatic_continuation']['actual_arguments']['char_offset'],8000)
                    return call('write_file',path='result.txt',content=last['text'],expected_sha256='')
                return {'message':{'content':'已保存文件尾部。'}}
        with patch.object(self.tools,'read_file',wraps=self.tools.read_file) as read:
            result = run_work(Client(),'提取 a.js 文件尾部并保存 result.txt',[],self.tools,lambda:False,lambda _:None,lambda _:None)
        self.assertEqual(result['outcome'],'completed')
        self.assertEqual(result['model_recovery']['read_continuations'],2)
        self.assertEqual(read.call_count,3)
        self.assertEqual([s['arguments']['char_offset'] for s in result['steps'] if s['tool']=='read_file'],[0,4000,8000])
        self.assertEqual((self.root/'result.txt').read_text(),'TAIL_SENTINEL')

    def test_batch_repetition_advances_each_file_without_replaying_the_batch(self):
        tools = SimonWorkTools(self.root,self.data,'batch-cursor',lambda *args:False)
        tools.auto_context = False
        self.addCleanup(tools.close)
        for name in ('a.txt','b.txt'):
            (self.root/name).write_text(name[0]*7000)
        class Client:
            model = 'fixture'
            count = 0
            def request(self,*args):
                self.count += 1
                if self.count <= 7:return call('read_files',paths=['a.txt','b.txt'])
                return {'message':{'content':'两个文件均已读取。'}}
        with patch.object(tools,'read_file',wraps=tools.read_file) as read:
            result = run_work(Client(),'读取两个文件',[],tools,lambda:False,lambda _:None,lambda _:None)
        self.assertEqual(result['outcome'],'completed')
        self.assertEqual(read.call_count,4)
        continued = [s for s in result['steps'] if 'automatic_continuation' in s['result']]
        self.assertEqual([s['arguments']['path'] for s in continued],['a.txt','b.txt'])
        self.assertTrue(all(s['arguments']['char_offset']==4000 for s in continued))

    def test_fully_read_files_still_stop_persistent_empty_progress(self):
        (self.root/'a.txt').write_text('complete')
        class Client:
            model = 'fixture'
            def request(self,*args):return call('read_file',path='a.txt')
        with patch.object(self.tools,'read_file',wraps=self.tools.read_file) as read:
            result=run_work(Client(),'检查状态',[],self.tools,lambda:False,lambda _:None,lambda _:None)
        self.assertEqual(result['outcome'],'incomplete')
        self.assertEqual(result['model_recovery']['read_continuations'],0)
        read.assert_called_once()


if __name__=='__main__':unittest.main()
