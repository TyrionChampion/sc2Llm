import json
from pathlib import Path
import tempfile
import unittest
from arena.match_review import load_review,require_review_read,rehearse_dispatch
from arena.skill_lab import SkillLab
from arena.python_skills import PythonCatalog

class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.old=self.root/'old';self.old.mkdir()
        (self.old/'run.json').write_text(json.dumps({'models':['provider/qwen','provider/glm']}))
        commanders=[]
        for i in range(2):
            lab=SkillLab(self.old/f'p{i+1}/lab');lab.setup()
            lab.write('skills/tactic.py','def tick(ctx,state): return {}')
            lab.write('skills/tactic.json',json.dumps({'name':'tactic','file':'tactic.py','unit':'marine','description':f'own {i}','actions':['move'],'parameters':{}}))
            lab.write('tests/test_tactic.py',f'owner={i}')
            journal=self.old/f'journal{i}.jsonl'
            journal.write_text(json.dumps({'record':'feedback_event','event':{'id':1,'kind':'plan_rejected','game_loop':224,'error':f'private player {i}'}})+'\n')
            commanders.append({'feedback':{'journal':str(journal)}})
        (self.old/'match.json').write_text(json.dumps({'outcome':'interrupted','commanders':commanders}))
    def test_only_own_source_feedback_and_actual_read_gate(self):
        lab=SkillLab(self.root/'new');lab.setup()
        data=load_review(self.old,1,'provider/glm',lab)
        self.assertEqual(data['player'],2)
        self.assertIn('private player 1',lab.read('fixtures/prior_event_1.json'))
        self.assertNotIn('private player 0',lab.read('fixtures/prior_event_1.json'))
        self.assertEqual(lab.read('tests/test_tactic.py'),'owner=1')
        lab.audit('run_tests',{},{});
        with self.assertRaises(ValueError):require_review_read(lab)
        lab.audit('read_file',{'path':'fixtures/last_match_review.json'},{});require_review_read(lab)
    def test_wrong_author_and_running_source_rejected(self):
        lab=SkillLab(self.root/'new');lab.setup()
        with self.assertRaisesRegex(ValueError,'author/model'):load_review(self.old,1,'provider/qwen',lab)
        match=json.loads((self.old/'match.json').read_text());match['outcome']='running'
        (self.old/'match.json').write_text(json.dumps(match))
        with self.assertRaisesRegex(ValueError,'stopped'):load_review(self.old,1,'provider/glm',lab)
        self.assertFalse(list((lab.root/'skills').iterdir()))
    def test_real_dispatch_validator_rejects_missing_fields_and_sends_error_back(self):
        catalog=PythonCatalog(self.old/'p1/lab/skills')
        class Client:
            index=0;closed=False;n=0
            def audit(self,r):pass
            def chat(self,s,u):
                self.n+=1
                if self.n==1:return '{"tasks":[{"skill":"tactic","args":{"count":4,"target":"enemy_start"}}]}'
                payload=json.loads(u)
                assert 'retreat_target' in payload['rehearsal_feedback']['error']
                return '{"tasks":[{"skill":"tactic","args":{"count":4,"target":"enemy_start","retreat_target":"home","timeout_s":120,"params":{}}}]}'
        result=rehearse_dispatch(Client(),catalog,'reviewed')
        self.assertTrue(result['passed']);self.assertEqual(result['attempt'],2)
    def test_exhausted_invalid_dispatch_never_passes(self):
        catalog=PythonCatalog(self.old/'p1/lab/skills')
        class Client:
            index=0;closed=False
            def audit(self,r):pass
            def chat(self,s,u):return '{"tasks":[]}'
        with self.assertRaisesRegex(RuntimeError,'after 3 attempts'):rehearse_dispatch(Client(),catalog,'reviewed')

    def test_qualification_is_five_consecutive_valid_decisions_and_rejects_late(self):
        from arena.match_review import qualify_battle
        from unittest.mock import patch
        catalog=PythonCatalog(self.old/'p1/lab/skills')
        class Client:
            index=0;closed=False;n=0
            def audit(self,r):pass
            def chat(self,s,u):
                self.n+=1
                return '{"tasks":[{"skill":"train_worker","count":1}],"reason":"economy"}'
        c=Client();result=qualify_battle(c,catalog,'brief')
        self.assertEqual(result['consecutive_decisions'],5);self.assertEqual(c.n,5)
        with patch('time.monotonic',side_effect=[0,23]),self.assertRaisesRegex(RuntimeError,'22s'):
            qualify_battle(Client(),catalog,'brief')
    def test_coaching_preserves_error_and_requires_a_fresh_complete_round(self):
        from arena.match_review import qualify_with_coaching
        from unittest.mock import patch
        class Client:
            index=0;closed=False
        c=Client()
        def attempt(client,catalog,strategy):
            if not client.qualification_feedback:raise RuntimeError('unknown build_barracks')
            self.assertEqual(client.qualification_feedback,['unknown build_barracks'])
            return {'passed':True,'consecutive_decisions':5}
        with patch('arena.match_review.qualify_battle',side_effect=attempt) as check:
            report=qualify_with_coaching(c,None,'strategy')
        self.assertEqual(check.call_count,2);self.assertEqual(report['round'],2)
        with patch('arena.match_review.qualify_battle',side_effect=RuntimeError('bad args')) as check:
            with self.assertRaisesRegex(RuntimeError,'Did not pass'):
                qualify_with_coaching(Client(),None,'strategy')
        self.assertEqual(check.call_count,3)

    def test_copy_fixtures_preserves_current_host_case(self):
        from arena.lab_prepare import copy_lab_fixtures
        old=SkillLab(self.old/'p1/lab');new=SkillLab(self.root/'new');new.setup()
        (old.root/'fixtures/failure.json').write_text('{"error":"old actual failure"}')
        (old.root/'fixtures/execution_race.json').write_text('{"unsafe":"old fixture"}')
        copy_lab_fixtures(old,new)
        self.assertIn('old actual failure',new.read('fixtures/failure.json'))
        self.assertIn('execution_context',new.read('fixtures/execution_race.json'))

if __name__=='__main__':unittest.main()
