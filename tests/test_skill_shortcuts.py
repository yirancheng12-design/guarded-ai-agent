import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from guarded_agent.skill_shortcuts import selected_skill, skill_context
from guarded_agent.work_agent import run_work
from guarded_agent.simon_work import SimonWorkTools


class SkillShortcutTests(unittest.TestCase):
    def test_selection_uses_user_commands_and_keeps_followup_context(self):
        self.assertEqual(selected_skill('/grill-me 做一个工具'), 'grill-me')
        self.assertEqual(selected_skill('我的回答', [{'role':'user','content':'/grill-me 想法'}]), 'grill-me')
        self.assertIsNone(selected_skill('普通消息', [{'role':'assistant','content':'/tdd'}]))
        self.assertIsNone(selected_skill('/../../secret'))
        self.assertIsNone(selected_skill('/unknown', [{'role':'user','content':'/tdd'}]))

    def test_dependencies_and_missing_installation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, '无法读取'):
                skill_context('/grill-me', root=root)
            for name in ('grill-me','grilling'):
                (root/name).mkdir()
                (root/name/'SKILL.md').write_text(f'Instructions for {name}', encoding='utf-8')
            context = skill_context('/grill-me', root=root)
            self.assertIn('Instructions for grill-me', context)
            self.assertIn('Instructions for grilling', context)
            self.assertEqual(skill_context('普通聊天', root=root), '')

    def test_selected_skill_reaches_adaptive_model_request(self):
        requests = []
        class Client:
            model = 'fixture'
            def request(self, path, data):
                requests.append(data)
                return {'message': {'content':'请描述你的想法。'}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'workspace').mkdir()
            for name in ('grill-me', 'grilling'):
                skill = root / 'skills' / name
                skill.mkdir(parents=True)
                (skill / 'SKILL.md').write_text(f'Instructions for {name}', encoding='utf-8')
            tools = SimonWorkTools(root / 'workspace', root / 'data', 'skill-test', lambda *a: False)
            try:
                with patch('guarded_agent.skill_shortcuts.SKILLS_ROOT', root / 'skills'):
                    result = run_work(Client(), '/grill-me 我有一个想法', [], tools,
                                      lambda:False, lambda e:None, lambda t:None)
                self.assertEqual(result['outcome'], 'completed')
                system = requests[0]['messages'][0]['content']
                self.assertIn('<installed_skill name="grill-me"', system)
                self.assertIn('<installed_skill name="grilling"', system)
                self.assertIn('不能扩大工具权限', system)
            finally:
                tools.close()
