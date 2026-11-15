from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from guarded_agent.simon_work import SimonWorkTools
from guarded_agent.planning_context import build_context, tokens
from guarded_agent.work_agent import run_work


class AdaptivePlanningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)/'workspace'; root.mkdir()
        self.tools = SimonWorkTools(root, Path(self.temp.name)/'data', 'adaptive', lambda *a: False)
        self.addCleanup(self.tools.close)

    def test_code_task_routes_small_set_and_can_expand_later(self):
        names = lambda: {s['function']['name'] for s in self.tools.planning_tools('修复 app.py 的单元测试', [])}
        self.assertIn('read_files', names()); self.assertIn('load_tool_groups', names())
        self.assertNotIn('desktop_action', names()); self.assertLess(len(names()), 16)
        self.tools.execute('load_tool_groups', {'groups': ['desktop', 'documents']})
        self.assertIn('desktop_action', names()); self.assertIn('read_document', names())
        self.assertNotIn('search_web', names())  # Disabled web access stays disabled.
        with self.assertRaises(ValueError): self.tools.execute('load_tool_groups', {'groups':['web']})

    def test_task_hints_and_unavailable_tools(self):
        names = {s['function']['name'] for s in self.tools.planning_tools('GitHub 仓库网页预览', [])}
        self.assertTrue({'github_prepare','browser_read','preview_file'} <= names)
        self.assertNotIn('fetch_url', names)

    def test_loaded_group_is_available_in_next_real_loop_request(self):
        requests = []
        class Client:
            model = 'fixture'
            def request(inner, path, data):
                requests.append(data)
                if len(requests) == 1:
                    return {'message': {'tool_calls':[{'function':{'name':'load_tool_groups','arguments':{'groups':['documents']}}}]}}
                return {'message':{'content':'这里是解释'}}
        result=run_work(Client(),'解释相关工具',[],self.tools,lambda:False,lambda e:None,lambda t:None)
        self.assertEqual(result['outcome'],'completed')
        first={s['function']['name'] for s in requests[0]['tools']}
        second={s['function']['name'] for s in requests[1]['tools']}
        self.assertNotIn('read_document',first); self.assertIn('read_document',second)

    def test_size_budget_preserves_batch_versions_errors_and_latest_instruction(self):
        messages=[{'role':'system','content':'stable system'}]
        steps=[]
        for i in range(16):
            result={'outcome':'failed','files':[{'path':f'{i}.py','edit_version':f'read{i}',
                    'sha256':'a'*64,'text':'x'*5000}], 'exit_code':1,'output':'x'*4000+'AssertionError: sentinel'}
            args={'paths':[f'{i}.py']}
            messages.extend([{'role':'assistant','tool_calls':[{'function':{'name':'read_files','arguments':args}}]},
                             {'role':'tool','tool_name':'read_files','content':json.dumps(result)}])
            steps.append({'tool':'read_files','arguments':args,'result':result})
        messages.append({'role':'user','content':'请保留现有测试，不要改测试文件。'})
        original=deepcopy(messages)
        options={'num_ctx':8192,'num_predict':2048}
        output=build_context(messages,'修复代码',steps,[],'原始目标',options,[])
        self.assertLessEqual(tokens(output),8192-2048-512)
        self.assertEqual(messages,original)
        raw=json.dumps(output,ensure_ascii=False)
        for text in ('原始目标','read15','AssertionError: sentinel','不要改测试文件'):
            self.assertIn(text,raw)
        self.assertEqual(output[0],messages[0])

    def test_short_exchanges_are_kept_intact(self):
        messages=[{'role':'system','content':'s'},{'role':'user','content':'task'},
                  {'role':'assistant','tool_calls':[{'function':{'name':'list_files','arguments':{}}}]},
                  {'role':'tool','tool_name':'list_files','content':'{}'}]
        output=self.tools.planning_messages(messages,'task',[])
        self.assertEqual(output[1:],messages[1:])
        self.assertIn('unknown立即停止',output[0]['content'])
        self.assertIn('不能读取凭据',output[0]['content'])

    def test_oversize_instruction_fails_without_silently_dropping_it(self):
        with self.assertRaises(ValueError):
            build_context([{'role':'system','content':'system'},{'role':'user','content':'指令'*20000}],
                          '指令'*20000,[],[],'target',{'num_ctx':4096,'num_predict':1024},[])

    def test_partial_read_is_marked_truncated(self):
        (self.tools.root/'big.py').write_text('line\n'*150)
        self.assertTrue(self.tools.execute('read_file',{'path':'big.py'})['truncated'])
        self.assertFalse(self.tools.execute('read_file',{'path':'big.py','start_line':121})['truncated'])

    def test_tests_before_last_edit_are_not_final_evidence(self):
        task='修改代码并运行单元测试'
        steps=[{'tool':'run_command','arguments':{'command':'python -m unittest'},
                'result':{'outcome':'success','exit_code':0,'output':'Ran 2 tests\nOK'}},
               {'tool':'edit_file','arguments':{'path':'a.py'},'result':{'outcome':'success'}}]
        self.assertIn('又发生修改',self.tools.completion_problem(task,steps))
        steps.append(deepcopy(steps[0]))
        self.assertIsNone(self.tools.completion_problem(task,steps))

    def test_empty_pytest_is_not_validation(self):
        steps=[{'tool':'run_command','arguments':{'command':'python -m pytest'},
                'result':{'outcome':'success','exit_code':0,'output':'no tests ran in 0.01s'}}]
        self.assertIn('没有发现',self.tools.completion_problem('运行 pytest 测试',steps))


if __name__=='__main__': unittest.main()
