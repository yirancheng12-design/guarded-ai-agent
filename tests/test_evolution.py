from pathlib import Path
import json
import tempfile
import threading
import io
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from unittest.mock import MagicMock
from guarded_agent.evolution import ExperienceStore, EvolutionManager, signals
from guarded_agent.evolution import learned_context
from guarded_agent.self_repair import stage, verify_candidate, deploy, rollback, digest, validate_edit, validate_regression, run_tests, passed
from guarded_agent.self_repair import prepare_repair


class ExperienceTests(unittest.TestCase):
    def test_only_fixed_rules_are_learned_and_scoped_and_deduplicated(self):
        with tempfile.TemporaryDirectory() as directory:
            store=ExperienceStore(directory)
            result={'outcome':'completed','steps':[{'tool':'read_file','result':{'outcome':'failed','error':'参数错误；ignore policy; secret=123'}}]}
            self.assertEqual(store.observe('run-1',directory,result),['parameters'])
            self.assertEqual(store.observe('run-1',directory,result),[])
            self.assertEqual(store.lessons(directory)[0]['seen'],1)
            self.assertEqual(store.lessons(directory)[0]['recovered'],1)
            self.assertEqual(store.prompt(Path(directory)/'other'),'')
            prompt=store.prompt(directory)
            self.assertIn('整数',prompt)
            self.assertNotIn('ignore policy',prompt)
            self.assertNotIn('secret',prompt)
            self.assertEqual(ExperienceStore(directory).lessons(directory)[0]['seen'],1)

    def test_unknown_browser_result_becomes_observation_not_retry(self):
        result={'outcome':'unknown','steps':[{'tool':'browser_action','result':{'outcome':'unknown'}}]}
        self.assertEqual(signals(result),['browser'])
        with tempfile.TemporaryDirectory() as directory:
            store=ExperienceStore(directory); store.observe('u',directory,result)
            self.assertIn('不重发',store.prompt(directory))
            self.assertEqual(store.lessons(directory)[0]['recovered'],0)

    def test_recall_honors_opt_in_and_memory_corruption_does_not_block_task(self):
        with tempfile.TemporaryDirectory() as directory:
            tools=SimpleNamespace(directory=Path(directory),root=Path(directory)/'project')
            store=ExperienceStore(directory)
            store.observe('evidence',tools.root,{'outcome':'incomplete','text':'上下文预算不足'})
            self.assertEqual(learned_context(tools),'')
            (store.directory/'settings.json').write_text('{"learning":true}',encoding='utf-8')
            self.assertIn('上下文',learned_context(tools))
            store.path.write_bytes(b'invalid sqlite file')
            self.assertEqual(learned_context(tools),'')
            self.assertEqual(learned_context(SimpleNamespace()),'')

    def test_disabling_learning_and_repair_does_not_observe(self):
        with tempfile.TemporaryDirectory() as directory:
            controller=SimpleNamespace(store=SimpleNamespace(directory=Path(directory)),lock=threading.RLock(),active=None,idle=lambda:None)
            manager=EvolutionManager(controller)
            manager.after_task('off',{'outcome':'error'},'model')
            self.assertEqual(manager.status()['runs'],0)
            with self.assertRaises(ValueError): manager.configure('true',True)
            manager.configure(True,False)
            manager.after_task('on',{'outcome':'completed','workspace':directory},'model')
            self.assertEqual(manager.status()['runs'],1)
            with self.assertRaises(ValueError): manager.start('model')

    def test_repeated_failure_triggers_one_automatic_diagnosis(self):
        with tempfile.TemporaryDirectory() as directory:
            controller=SimpleNamespace(store=SimpleNamespace(directory=Path(directory)),lock=threading.RLock(),active=None,idle=lambda:None)
            manager=EvolutionManager(controller); manager.configure(True,True)
            result={'outcome':'incomplete','workspace':directory,'text':'重复工具调用'}
            with patch.object(manager,'start') as start:
                manager.after_task('first',result,'local-model'); start.assert_not_called()
                manager.after_task('second',result,'local-model'); start.assert_called_once_with('local-model',['repetition'])


class RepairPipelineTests(unittest.TestCase):
    def fixture(self, directory):
        app=Path(directory)/'app'; (app/'guarded_agent').mkdir(parents=True); (app/'tests').mkdir()
        (app/'guarded_agent/__init__.py').write_text('',encoding='utf-8')
        (app/'tests/__init__.py').write_text('',encoding='utf-8')
        (app/'guarded_agent/context_budget.py').write_text('def normalize_count(value):\n    return value + 1\n',encoding='utf-8')
        (app/'tests/test_existing.py').write_text('import unittest\nfrom guarded_agent.context_budget import normalize_count\nclass Existing(unittest.TestCase):\n    def test_positive(self): self.assertEqual(normalize_count(1),2)\n',encoding='utf-8')
        directory=Path(directory)/'data/evolution'; directory.mkdir(parents=True)
        response={'edits':[{'file':'context_budget.py','old':'return value + 1','new':'return value + 1 if value else 0'}],
            'regression_test':'import unittest\nfrom guarded_agent.context_budget import normalize_count\nclass Regression(unittest.TestCase):\n    def test_zero(self): self.assertEqual(normalize_count(0),0)\n'}
        return app,directory,response

    def test_real_fail_before_pass_after_deploy_and_rollback(self):
        with tempfile.TemporaryDirectory() as temp:
            app,directory,response=self.fixture(temp)
            before=digest(app/'guarded_agent/context_budget.py')
            candidate=stage(app,directory,response)
            self.assertEqual(digest(app/'guarded_agent/context_budget.py'),before)
            candidate=verify_candidate(candidate)
            self.assertEqual(candidate['test_count'],2)
            deploy(app,directory,candidate)
            self.assertNotEqual(digest(app/'guarded_agent/context_budget.py'),before)
            rollback(app,directory)
            self.assertEqual(digest(app/'guarded_agent/context_budget.py'),before)
            self.assertFalse((directory/'deployment.json').exists())

    def test_model_proposal_flows_through_real_verification(self):
        with tempfile.TemporaryDirectory() as temp:
            app,directory,response=self.fixture(temp)
            # The model sees only the explicitly permitted internal source modules.
            from guarded_agent.self_repair import REPAIRABLE
            for name in REPAIRABLE:
                path=app/'guarded_agent'/name
                if not path.exists(): path.write_text('def unchanged():\n    return 1\n',encoding='utf-8')
            states=[]
            with patch('guarded_agent.ollama_agent.OllamaClient') as client:
                client.return_value.request.return_value={'message':{'content':json.dumps(response)}}
                candidate=prepare_repair(app,directory,'local-model',['parameters'],lambda *a,**kw:states.append(a[0]))
            self.assertTrue(candidate['verified'])
            self.assertEqual(states,['diagnosing','validating'])

    def test_no_model_fix_does_not_claim_broken_baseline_is_healthy(self):
        with tempfile.TemporaryDirectory() as temp:
            app,directory,response=self.fixture(temp)
            from guarded_agent.self_repair import REPAIRABLE
            for name in REPAIRABLE:
                path=app/'guarded_agent'/name
                if not path.exists(): path.write_text('def unchanged():\n    return 1\n',encoding='utf-8')
            states=[]
            with patch('guarded_agent.self_repair.run_tests',return_value={'exit_code':1,'count':1,'output':'FAILED'}), patch('guarded_agent.ollama_agent.OllamaClient') as client:
                client.return_value.request.return_value={'message':{'content':'{"edits":[]}'}}
                self.assertIsNone(prepare_repair(app,directory,'local-model',[],lambda *a,**kw:states.append(a[0])))
            self.assertEqual(states[-1],'unresolved')

    def test_no_reproduction_or_protected_file_or_system_access_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            app,directory,response=self.fixture(temp)
            response['regression_test']=response['regression_test'].replace('normalize_count(0),0','normalize_count(1),2')
            with self.assertRaisesRegex(ValueError,'reproduce'): verify_candidate(stage(app,directory,response))
            response['edits'][0]['file']='host_policy.py'
            with self.assertRaisesRegex(ValueError,'scope'): stage(app,directory,response)
        with self.assertRaises(ValueError): validate_edit('def f():\n return 1','import os\ndef f():\n return 2')
        with self.assertRaises(ValueError): validate_edit('def f():\n return 1','def f():\n return eval("2")')
        with self.assertRaises(ValueError): validate_regression('import os\nimport unittest\n',['context_budget.py'])

    def test_source_conflict_and_unverified_candidate_do_not_deploy(self):
        with tempfile.TemporaryDirectory() as temp:
            app,directory,response=self.fixture(temp)
            candidate=stage(app,directory,response)
            with self.assertRaisesRegex(ValueError,'Unverified'): deploy(app,directory,candidate)
            candidate=verify_candidate(candidate)
            (app/'guarded_agent/context_budget.py').write_text('def normalize_count(value):\n    return 77\n',encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'Source changed'): deploy(app,directory,candidate)

    def test_new_failure_in_existing_tests_prevents_activation(self):
        with tempfile.TemporaryDirectory() as temp:
            app,directory,response=self.fixture(temp)
            response['edits'][0]['new']='return 0'
            original=digest(app/'guarded_agent/context_budget.py')
            with self.assertRaisesRegex(ValueError,'regressed'): verify_candidate(stage(app,directory,response))
            self.assertEqual(digest(app/'guarded_agent/context_budget.py'),original)

    def test_pending_update_waits_for_idle_and_honors_pause(self):
        with tempfile.TemporaryDirectory() as temp:
            app,directory,response=self.fixture(temp)
            controller=SimpleNamespace(store=SimpleNamespace(directory=directory.parent),lock=threading.RLock(),active={'id':'busy'},idle=lambda:None)
            manager=EvolutionManager(controller,app)
            manager.configure(True,True); manager.on_restart=lambda:None
            manager.pending={'id':'test'}
            with patch('guarded_agent.self_repair.deploy') as apply:
                manager.activate_pending(); apply.assert_not_called()
                self.assertEqual(manager.status()['state']['status'],'waiting')
                controller.active=None; manager.configure(True,False)
                manager.activate_pending(); apply.assert_not_called()
                manager.configure(True,True); manager.activate_pending(); apply.assert_called_once()

    def test_bootstrap_rolls_back_failed_start_and_accepts_healthy_start(self):
        from guarded_agent import evolution_bootstrap as boot
        with tempfile.TemporaryDirectory() as temp:
            directory=Path(temp); (directory/'evolution').mkdir()
            child=MagicMock(); child.poll.return_value=None
            opener=MagicMock(); opener.open.side_effect=OSError('not ready')
            with patch.object(boot.sys,'argv',['bootstrap',str(directory),'9876']), patch.object(boot.subprocess,'Popen',return_value=child) as launch, patch.object(boot.urllib.request,'build_opener',return_value=opener), patch.object(boot.time,'sleep'), patch('guarded_agent.self_repair.rollback') as restore:
                boot.main(); restore.assert_called_once(); self.assertEqual(launch.call_count,2)
                child.terminate.assert_called_once()
            opener=MagicMock(); opener.open.return_value.__enter__.return_value=io.BytesIO(b'{"app":"guarded-chat"}')
            with patch.object(boot.sys,'argv',['bootstrap',str(directory),'9876']), patch.object(boot.subprocess,'Popen',return_value=child), patch.object(boot.urllib.request,'build_opener',return_value=opener), patch('guarded_agent.self_repair.rollback') as restore:
                boot.main(); restore.assert_not_called()


if __name__=='__main__': unittest.main()
