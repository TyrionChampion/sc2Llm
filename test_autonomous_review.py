"""Reviews require real evidence; repairs must reproduce failure before applying."""
import json
from pathlib import Path
import tempfile
import sys
from types import SimpleNamespace
import unittest
import gzip
from unittest.mock import patch

from arena.autonomous_bot import RequestBudget
from arena.autonomous_review import repair_game, review_game, compact_review_evidence


class ReviewClient:
    result={}
    def __init__(self,*args):pass
    def chat(self,*args):return json.dumps(self.result)
    def close(self):pass


class ReviewTests(unittest.TestCase):
    def test_actual_terran_review_keeps_all_worlds_with_the_go_input_guard(self):
        from arena.go_planner import GO_MODEL
        from arena.context_tables import decode_tables
        fixture=json.loads(gzip.decompress((Path(__file__).parent/'testdata/duel-terran-review-diagnostics.json.gz').read_bytes()))
        diagnostic=fixture['diagnostics']
        captured={}
        class Client(ReviewClient):
            def chat(self,rules,observation):
                captured.update(rules=rules,observation=observation)
                return json.dumps(dict(tasks=[],category='strategy',strategy='比较机械防反与生化侧翼，依据侦察决定。'))
        budget=RequestBudget(2)
        with patch('arena.autonomous_review.GoPlanner',Client):
            review_game(GO_MODEL,'/tmp/unused-review-fixture',budget,diagnostic,fixture['strategy'],'LLM_duel',
                        review_context=fixture['review_context'])
        self.assertEqual(budget.used,1)
        self.assertLessEqual(len(captured['rules'])+len(captured['observation']),90000)
        evidence=decode_tables(json.loads(captured['observation']))['evidence']
        worlds=[row.get('world',None) if 'world' in row else evidence['timeline_worlds'][row['world_index']]
                for row in evidence['timeline']]
        self.assertEqual(worlds,[row['world'] for row in diagnostic['timeline']])
    def test_large_review_roundtrips_every_world_and_task_change(self):
        from arena.context_tables import decode_tables
        task={'id':1,'skill':'train_unit','state':'running','count':20,
              'workflow':{'args':{'unit':'Marine'},'issued_quantity':7}}
        worlds=[dict(game_s=i,minerals=i*31,race='terran',
                     units=[{'tag':j,'health':45,'position':[j,i]} for j in range(120)])
                for i in range(40)]
        original={'errors':[],'timeline':[dict(world=w,active_tasks=[task] if i<39 else [])
                  for i,w in enumerate(worlds)]}
        result=compact_review_evidence(original,[])
        restored=decode_tables(result['timeline_worlds'])
        self.assertEqual([restored[row['world_index']] for row in result['timeline']],worlds)
        self.assertEqual(result['timeline'][0]['task_changes'][0]['execution']['issued_quantity'],7)
        self.assertEqual(result['timeline'][-1]['tasks_no_longer_active'],[1])
        self.assertEqual(original['timeline'][0]['world'],worlds[0])

    def test_review_keeps_every_world_and_tracks_task_removal(self):
        task={'id':1,'skill':'train_unit','state':'running','count':6,
              'workflow':{'args':{'unit':'marine'},'issued_quantity':2}}
        original={'errors':[],'timeline':[{'world':{'game_s':10},'active_tasks':[task]},
                  {'world':{'game_s':20},'active_tasks':[task]},
                  {'world':{'game_s':30},'active_tasks':[]}]}
        result=compact_review_evidence(original,[])
        self.assertEqual([row['world'] for row in result['timeline']],
                         [row['world'] for row in original['timeline']])
        self.assertEqual(result['timeline'][1]['task_changes'],[])
        self.assertEqual(result['timeline'][2]['tasks_no_longer_active'],[1])
        self.assertEqual(len(original['timeline'][1]['active_tasks']),1)

    def test_feature_gap_cannot_cite_invented_events(self):
        ReviewClient.result=dict(tasks=[],category='feature_gap',strategy='Produce marines',
            gap=dict(evidence_ids=[99],reproduction='Reproduce actual issue'))
        with patch('arena.autonomous_review.ChatGPTSubscriptionPlanner',ReviewClient):
            with self.assertRaisesRegex(ValueError,'nonexistent evidence'):
                review_game('chatgpt/gpt-6-luna','/tmp/unused',RequestBudget(2),
                            {'errors':[{'id':1,'kind':'failed','error':'real failure'}]},'initial','easy')

    def test_combat_summary_retains_deaths_without_calling_fog_exits_deaths(self):
        windows=[{'id':i,'status':'closed','enemy_deaths_observed':{'SCV':2},
                  'enemy_left_vision':{'Marine':3},'damage_observed':True} for i in range(8)]
        result=compact_review_evidence({'errors':[],'engagement_windows':windows},[])
        self.assertEqual(result['combat_summary']['enemy_deaths_observed'],{'SCV':16})
        self.assertEqual(result['combat_summary']['enemy_left_vision'],{'Marine':24})
        self.assertEqual(result['omitted_engagement_windows'],2)
        self.assertEqual(len(result['engagement_windows']),6)

    def test_strategy_review_consumes_one_budget_slot(self):
        ReviewClient.result=dict(tasks=[],category='strategy',strategy='Build two barracks, no early expansion')
        budget=RequestBudget(2)
        with patch('arena.autonomous_review.ChatGPTSubscriptionPlanner',ReviewClient):
            result=review_game('chatgpt/gpt-6-luna','/tmp/unused',budget,{'errors':[]},'initial','easy')
        self.assertEqual(result['category'],'strategy');self.assertEqual(budget.by_role,{'review':1})

    def repair_fixture(self,regression,protected=False):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'root';root.mkdir();(root/'arena').mkdir()
            (root/'arena/__init__.py').write_text('')
            (root/'arena/macro.py').write_text('def value():\n    return 1\n')
            (root/'test_existing.py').write_text(
                'import unittest\nfrom arena.macro import value\nclass Existing(unittest.TestCase):\n'
                ' def test_positive(self):self.assertGreater(value(),0)\n')
            (root/'.venv311/bin').mkdir(parents=True)
            python=Path(sys.executable)
            (root/'.venv311/bin/python').symlink_to(python)
            folder=Path(d)/'match/repair';folder.parent.mkdir()
            (folder.parent/'diagnostics.json').write_text('{"errors":[]}')
            class Process:
                returncode=0
                def __init__(self,command,**kwargs):
                    self.stdin=self.stdout=self.stderr=SimpleNamespace(close=lambda:None)
                    self.candidate=Path(command[command.index('-C')+1])
                    self.answer=Path(command[command.index('-o')+1])
                def communicate(self,*args,**kwargs):
                    (self.candidate/'arena/macro.py').write_text('def value():\n    return 2\n')
                    (self.candidate/'test_autonomy_regression_value.py').write_text(regression)
                    if protected:(self.candidate/'test_existing.py').write_text('disabled')
                    self.answer.write_text(json.dumps(dict(changed=True,
                        regression_modules=['test_autonomy_regression_value'],summary='fixture fix')))
                    return '', ''
            login=lambda *args:SimpleNamespace(binary='fixture',env={},model='gpt-6-luna',close=lambda:None)
            with patch('arena.autonomous_review.ChatGPTSubscriptionPlanner',login), \
                 patch('arena.autonomous_review.subprocess.Popen',Process), \
                 patch('arena.autonomous_review.CHECKS',['test_existing']):
                # Popen is patched only for generation; real subprocess tests use original Popen.
                from arena import autonomous_review
                original=autonomous_review.checked_tests
                import subprocess
                actual_popen=self.actual_popen
                def real_tests(*args):
                    with patch('arena.autonomous_review.subprocess.Popen',actual_popen):return original(*args)
                with patch('arena.autonomous_review.checked_tests',real_tests):
                    try:
                        result=repair_game(root,'chatgpt/gpt-6-luna',folder,RequestBudget(2),{})
                    except ValueError as error:
                        return str(error),(root/'arena/macro.py').read_text()
            return result,(root/'arena/macro.py').read_text()

    def setUp(self):
        import subprocess
        self.actual_popen=subprocess.Popen

    def test_repair_applies_only_after_actual_before_after_tests(self):
        regression=('import unittest\nfrom arena.macro import value\nclass Regression(unittest.TestCase):\n'
                    ' def test_value(self):self.assertEqual(value(),2)\n')
        result,source=self.repair_fixture(regression)
        self.assertTrue(result['applied']);self.assertEqual(result['regressions'],1)
        self.assertIn('return 2',source)

    def test_vacuous_regression_cannot_apply_a_repair(self):
        regression='import unittest\nclass Regression(unittest.TestCase):\n def test_nothing(self):pass\n'
        result,source=self.repair_fixture(regression)
        self.assertIn('failed before/after',result);self.assertIn('return 1',source)

    def test_existing_tests_are_protected(self):
        result,source=self.repair_fixture('import unittest\n',protected=True)
        self.assertIn('protected file',result);self.assertIn('return 1',source)


if __name__=='__main__':unittest.main()
