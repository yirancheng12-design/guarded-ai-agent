"""Fresh task state, durable continuation evidence, and bounded meta-tool recovery."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import unittest

from guarded_agent.work_agent import run_work
from guarded_agent.work_tools import WorkTools


def call(name, **arguments):
    return {'message': {'tool_calls': [{'function': {'name': name, 'arguments': arguments}}]}}


class TaskStatusRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/'workspace'
        self.root.mkdir()
        self.data = Path(self.temp.name)/'data'
        self.tools = self.make_tools('first')

    def make_tools(self, run):
        return WorkTools(self.root, self.data, run, lambda *args:False, conversation_id='same-conversation')

    def run_messages(self, tools, responses, task='继续'):
        requests = []
        class Client:
            model = 'fixture'
            def request(self,path,data):
                requests.append(data)
                return responses[len(requests)-1]
        result = run_work(Client(),task,[],tools,lambda:False,lambda _:None,lambda _:None)
        return result,requests

    def test_status_reports_current_checkpoint_without_mutating_previous(self):
        previous = {'objective':'检查状态','steps':[],'plan':[],'outcome':'incomplete'}
        self.tools.task_state = deepcopy(previous)
        actual = {'tool':'write_file','arguments':{'path':'saved.txt'},
                  'result':{'outcome':'success','path':'saved.txt','sha256':'a'*64}}
        self.tools.checkpoint('继续',[actual])
        status = self.tools.task_status()
        self.assertEqual(status['previous'],previous)
        self.assertEqual(self.tools.task_state,previous)
        self.assertEqual(status['current']['objective'],'检查状态')
        self.assertEqual(status['current']['step_count'],1)
        self.assertEqual(status['current']['recent_steps'][-1]['tool'],'write_file')
        self.assertEqual(status['current']['recent_steps'][-1]['result']['sha256'],'a'*64)

    def test_ignored_preloaded_status_recovers_to_a_real_write(self):
        self.tools.task_state = {'objective':'创建 saved.txt','steps':[],'plan':[]}
        responses = [call('task_status')]*3 + [
            call('write_file',path='saved.txt',content='DONE',expected_sha256=''),
            {'message':{'content':'已保存文件。'}}]
        with patch.object(self.tools,'execute',wraps=self.tools.execute) as execute:
            result,requests = self.run_messages(self.tools,responses)
        self.assertEqual(result['outcome'],'completed')
        self.assertEqual(result['model_recovery']['task_status_replays'],3)
        self.assertFalse(any(c.args[0]=='task_status' for c in execute.call_args_list))
        for request in requests:
            self.assertNotIn('task_status',{t['function']['name'] for t in request['tools']})
        self.assertEqual((self.root/'saved.txt').read_text(),'DONE')
        replay = next(json.loads(m['content']) for m in reversed(requests[3]['messages']) if m.get('role')=='tool')
        self.assertEqual(replay['current']['objective'],'创建 saved.txt')
        self.assertTrue(replay['cached'])

    def test_replayed_status_tracks_new_write_and_read(self):
        responses = [call('task_status'),call('write_file',path='a.txt',content='actual',expected_sha256=''),
                     call('task_status'),call('read_file',path='a.txt'),call('task_status'),
                     {'message':{'content':'已保存并回读。'}}]
        result,requests = self.run_messages(self.tools,responses,task='创建 a.txt')
        self.assertEqual(result['outcome'],'completed')
        for index,name in ((3,'write_file'),(5,'read_file')):
            replay = next(json.loads(m['content']) for m in reversed(requests[index]['messages']) if m.get('role')=='tool')
            self.assertEqual(replay['current']['recent_steps'][-1]['tool'],name)
        self.assertEqual(sum(s['tool']=='task_status' for s in result['steps']),1)

    def test_invented_status_update_fields_are_not_applied_or_dispatched(self):
        self.tools.task_state={'objective':'创建 real.txt','steps':[],'plan':[]}
        responses=[call('task_status',outcome='completed',artifacts='["fake.txt"]'),
                   call('write_file',path='real.txt',content='actual',expected_sha256=''),
                   {'message':{'content':'已保存。'}}]
        with patch.object(self.tools,'execute',wraps=self.tools.execute) as execute:
            result,requests=self.run_messages(self.tools,responses)
        replay=next(json.loads(m['content']) for m in reversed(requests[1]['messages']) if m.get('role')=='tool')
        self.assertEqual(replay['current']['outcome'],'running')
        self.assertEqual(replay['current']['artifacts'],[])
        self.assertEqual(set(replay['ignored_arguments']),{'outcome','artifacts'})
        self.assertFalse(any(c.args[0]=='task_status' for c in execute.call_args_list))
        self.assertEqual(result['outcome'],'completed')
        self.assertFalse((self.root/'fake.txt').exists())

    def test_original_actions_and_artifacts_survive_two_resumes(self):
        written = self.tools.execute('write_file',{'path':'a.txt','content':'saved','expected_sha256':''})
        original = [{'tool':'write_file','arguments':{'path':'a.txt','content':'saved','expected_sha256':''},'result':written},
                    {'tool':'run_command','arguments':{'command':'failed test'},
                     'result':{'outcome':'failed','exit_code':1,'output':'original failure'}}]
        self.tools.checkpoint('检查状态',original,'incomplete')
        second = self.make_tools('second')
        result,_ = self.run_messages(second,[call('read_file',path='a.txt'),{'message':{'content':'已核对文件，先前测试失败。'}}])
        self.assertEqual(result['outcome'],'completed')
        third = self.make_tools('third')
        tools = [s['tool'] for s in third.task_state['steps']]
        self.assertIn('write_file',tools)
        self.assertIn('run_command',tools)
        self.assertIn('read_file',tools)
        self.assertTrue(third.task_state['artifacts'])
        with patch.object(third,'run_command') as run:
            result,_ = self.run_messages(third,[call('run_command',command='failed test'),
                {'message':{'content':'原测试仍失败，未重发。'}}])
        run.assert_not_called()
        self.assertEqual(result['model_recovery']['repetition_recoveries'],1)

    def test_new_objective_does_not_inherit_previous_actions(self):
        self.tools.task_state = {'objective':'旧目标','steps':[{'tool':'write_file','arguments':{},'result':{}}],
                                'artifacts':[{'path':'old.txt'}]}
        self.tools.checkpoint('全新的任务',[])
        state = json.loads(self.tools.task_file.read_text(encoding='utf-8'))
        self.assertEqual(state['steps'],[])
        self.assertEqual(state['artifacts'],[])
        self.assertEqual(state['objective'],'全新的任务')

    def test_status_only_model_is_bounded_without_dispatching_side_effects(self):
        self.tools.task_state = {'objective':'检查状态','steps':[],'plan':[]}
        result,requests = self.run_messages(self.tools,[call('task_status')]*10)
        self.assertEqual(result['outcome'],'incomplete')
        self.assertEqual(len(requests),4)
        self.assertEqual([s['tool'] for s in result['steps']],['task_status'])
        self.assertEqual(list(self.root.iterdir()),[])

    def test_checkpoint_status_does_not_recursively_embed_older_states(self):
        self.tools.checkpoint('检查状态',[{'tool':'read_file','arguments':{'path':'a.py'},
            'result':{'path':'a.py','text':'old','sha256':'a'*64}}])
        for i in range(8):
            tools = self.make_tools(str(i))
            status = tools.task_status()
            tools.checkpoint('继续',[{'tool':'task_status','arguments':{},'result':status}])
        state = json.loads(tools.task_file.read_text(encoding='utf-8'))
        self.assertEqual([s['tool'] for s in state['steps']],['read_file','task_status'])
        self.assertLess(tools.task_file.stat().st_size,2000)


if __name__=='__main__': unittest.main()
