import json,tempfile,unittest
from pathlib import Path
from guarded_agent.work_tools import WorkTools
from guarded_agent.simon_work import SimonWorkTools
from guarded_agent.work_agent import run_work
from guarded_agent.work_progress import pending_action


class ReadPaginationTests(unittest.TestCase):
 def test_compaction_retains_executable_cursor_for_long_stylesheet(self):
  from guarded_agent.context_budget import compact_result
  (self.root/'long.css').write_text('中a'*12000,encoding='utf-8')
  page=self.tools.execute('read_file',{'path':'long.css'})
  for budget in (1000,1800):
   with self.subTest(budget=budget):
    compact=json.loads(compact_result(json.dumps(page,ensure_ascii=False),budget))
    self.assertEqual(compact['next_read'],page['next_read'])
    next_page=self.tools.execute('read_file',compact['next_read'])
    self.assertEqual(next_page['char_offset'],4000)
    self.assertEqual(compact['sha256'],next_page['sha256'])

 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
  self.root=Path(self.temp.name)/'workspace';self.root.mkdir()
  self.tools=WorkTools(self.root,Path(self.temp.name)/'data','paging',lambda *a:False)

 def test_single_long_line_can_be_read_to_the_end(self):
  text='prefix'+'中a'*13000+'TAIL_SENTINEL'
  (self.root/'min.css').write_text(text,encoding='utf-8')
  args={'path':'min.css'};pages=[];offsets=[]
  while args:
   result=self.tools.execute('read_file',args)
   pages.append(result['text']);offsets.append(result['char_offset'])
   self.assertLessEqual(len(result['text']),4000)
   args=result['next_read']
  self.assertEqual(''.join(pages),text)
  self.assertEqual(offsets,list(range(0,len(text),4000)))
  self.assertFalse(result['truncated'])

 def test_line_boundary_and_character_boundary_both_advance(self):
  lines=['a'*9000,'b','c'*7000,'d']
  (self.root/'multi.txt').write_text('\n'.join(lines))
  args={'path':'multi.txt','line_count':2};groups={}
  while args:
   result=self.tools.read_file(**args)
   groups.setdefault(result['start_line'],[]).append(result['text'])
   args=result['next_read']
  self.assertEqual(''.join(groups[1]),'\n'.join(lines[:2]))
  self.assertEqual(''.join(groups[3]),'\n'.join(lines[2:]))

 def test_actual_stylesheet_can_be_read_without_losing_a_character(self):
  original=Path(__file__).resolve().parents[1]/'guarded_agent/web_assets/style.css'
  text=original.read_text(encoding='utf-8-sig')
  (self.root/'style.css').write_text(text,encoding='utf-8')
  args={'path':'style.css','line_count':300};pages=[]
  while args:
   result=self.tools.read_file(**args);pages.append(result['text']);args=result['next_read']
  self.assertEqual(''.join(pages),'\n'.join(text.splitlines()))
  self.assertGreater(len(pages),1)

 def test_invalid_offsets_and_page_sizes_are_rejected(self):
  (self.root/'a.txt').write_text('abc')
  for args in ({'char_offset':-1},{'char_offset':99},{'max_chars':0},{'max_chars':20000},{'char_offset':True}):
   with self.subTest(args=args),self.assertRaises(ValueError):self.tools.read_file('a.txt',**args)

 def test_batch_returns_usable_cursors_and_file_versions(self):
  tools=SimonWorkTools(self.root,Path(self.temp.name)/'batch','batch',lambda *a:False)
  self.addCleanup(tools.close)
  (self.root/'a.css').write_text('x'*7000)
  first=tools.execute('read_files',{'paths':['a.css']})['files'][0]
  last=tools.execute('read_file',first['next_read'])
  self.assertEqual(first['sha256'],last['sha256'])
  self.assertIn('edit_version',last)
  self.assertEqual(first['text']+last['text'],'x'*7000)

 def test_duplicate_read_recovery_exposes_next_cursor(self):
  (self.root/'a.txt').write_text('x'*7000)
  requests=[]
  class Client:
   model='fixture'
   def request(inner,path,data):
    requests.append(data)
    if len(requests)<3:return {'message':{'tool_calls':[{'function':{'name':'read_file','arguments':{'path':'a.txt'}}}]}}
    if len(requests)==3:
     previous=next(json.loads(m['content']) for m in reversed(data['messages']) if m.get('role')=='tool')
     self.assertEqual(previous['next_read']['char_offset'],4000)
     return {'message':{'tool_calls':[{'function':{'name':'read_file','arguments':previous['next_read']}}]}}
    return {'message':{'content':'读取完成。'}}
  result=run_work(Client(),'读取 a.txt',[],self.tools,lambda:False,lambda e:None,lambda t:None)
  self.assertEqual(result['outcome'],'completed')
  self.assertEqual([s['result']['char_offset'] for s in result['steps']],[0,4000])

 def test_cannot_read_everything_at_once_is_not_a_blocker(self):
  self.assertTrue(pending_action('由于我无法一次性读取所有内容，我将分段读取文件。','读取文件'))
  self.assertFalse(pending_action('无法访问该文件，请提供正确路径。','读取文件'))

 def test_interleaved_reads_and_changed_line_counts_reuse_coverage(self):
  from guarded_agent.read_progress import ReadProgress
  (self.root/'script.js').write_text('x'*9000+'\n'+'y\n'*500)
  tracker=ReadProgress(self.tools)
  args={'path':'script.js','line_count':200}
  first=self.tools.execute('read_file',args);tracker.observe('read_file',args,first)
  for count in (100,150,300):
   repeated=tracker.repeated('read_file',{'path':'./script.js','line_count':count})
   self.assertIsNotNone(repeated)
   self.assertEqual(repeated['next_read']['char_offset'],4000)
  second=self.tools.execute('read_file',first['next_read'])
  tracker.observe('read_file',first['next_read'],second)
  self.assertEqual(tracker.repeated('read_file',args)['next_read']['char_offset'],8000)
  self.assertIsNone(tracker.repeated('read_file',{'path':'script.js','max_chars':16000}))

 def test_changed_file_can_be_read_again_even_with_identical_arguments(self):
  from guarded_agent.read_progress import ReadProgress
  (self.root/'a.txt').write_text('old')
  tracker=ReadProgress(self.tools);args={'path':'a.txt'}
  tracker.observe('read_file',args,self.tools.execute('read_file',args))
  self.assertIsNotNone(tracker.repeated('read_file',args))
  (self.root/'a.txt').write_text('new')
  self.assertIsNone(tracker.repeated('read_file',args))

 def test_batch_and_single_file_share_read_coverage(self):
  from guarded_agent.read_progress import ReadProgress
  tools=SimonWorkTools(self.root,Path(self.temp.name)/'tracked','tracked',lambda *a:False)
  tools.full_access=True
  self.addCleanup(tools.close)
  for name in ('a.txt','b.txt'):(self.root/name).write_text(name*2000)
  tracker=ReadProgress(tools);args={'paths':['a.txt','b.txt']}
  tracker.observe('read_files',args,tools.execute('read_files',args))
  self.assertIsNotNone(tracker.repeated('read_file',{'path':str(self.root/'a.txt')}))
  self.assertIsNotNone(tracker.repeated('read_files',args))
  self.assertIsNone(tracker.repeated('read_files',{'paths':['a.txt','missing.txt']}))

 def test_disjoint_pages_and_invalid_requests_are_not_treated_as_read(self):
  from guarded_agent.read_progress import ReadProgress
  (self.root/'a.txt').write_text('x'*16000)
  tracker=ReadProgress(self.tools)
  for offset in (0,8000):
   args={'path':'a.txt','char_offset':offset}
   tracker.observe('read_file',args,self.tools.execute('read_file',args))
  self.assertIsNone(tracker.repeated('read_file',{'path':'a.txt','max_chars':16000}))
  self.assertIsNone(tracker.repeated('read_file',{'path':'a.txt','char_offset':4000}))
  self.assertIsNone(tracker.repeated('read_file',{'path':'a.txt','char_offset':True}))

 def test_read_loop_recovers_across_intervening_tools_and_then_writes(self):
  from unittest.mock import patch
  (self.root/'a.txt').write_text('x'*7000);(self.root/'b.txt').write_text('context')
  def call(name,**args):return {'message':{'tool_calls':[{'function':{'name':name,'arguments':args}}]}}
  requests=[]
  responses=[call('read_file',path='a.txt',line_count=200),call('read_file',path='b.txt'),
             call('read_file',path='./a.txt',line_count=300),None,
             call('write_file',path='result.txt',content='done',expected_sha256=''),
             {'message':{'content':'已保存结果。'}}]
  class Client:
   model='fixture'
   def request(inner,path,data):
    requests.append(data);response=responses[len(requests)-1]
    if response is None:
     previous=next(json.loads(m['content']) for m in reversed(data['messages']) if m.get('role')=='tool')
     self.assertEqual(previous['execution_status'],'not_sent')
     return call('read_file',**previous['next_read'])
    return response
  with patch.object(self.tools,'read_file',wraps=self.tools.read_file) as read:
   result=run_work(Client(),'读取并保存结果',[],self.tools,lambda:False,lambda e:None,lambda t:None)
  self.assertEqual(result['outcome'],'completed')
  self.assertEqual(read.call_count,3)
  self.assertEqual((self.root/'result.txt').read_text(),'done')

 def test_resume_recognizes_old_reads_but_does_not_trust_changed_versions(self):
  from guarded_agent.read_progress import ReadProgress
  (self.root/'a.txt').write_text('old')
  args={'path':'a.txt'};previous=self.tools.execute('read_file',args)
  tracker=ReadProgress(self.tools);tracker.observe('read_file',args,previous)
  self.assertIsNotNone(tracker.repeated('read_file',args))
  (self.root/'a.txt').write_text('new')
  tracker=ReadProgress(self.tools);tracker.observe('read_file',args,previous)
  self.assertIsNone(tracker.repeated('read_file',args))

 def test_read_only_negative_clause_does_not_demand_a_write(self):
  tools=SimonWorkTools(self.root,Path(self.temp.name)/'validation','validation',lambda *a:False)
  self.addCleanup(tools.close)
  self.assertIsNone(tools.completion_problem('读取文件全部内容，不要重复读取、修改或搜索。',[]))
  self.assertIsNone(tools.completion_problem('检查代码，不要反复修改文件。',[]))
  self.assertIsNotNone(tools.completion_problem('不要修改 A，修改 B 文件。',[]))


if __name__=='__main__':unittest.main()
