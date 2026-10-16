"""Dual-player budgets and observation isolation without engine/model requests."""
import json
from pathlib import Path
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from go_duel_series import DurableBudget,PlayerBudget,player_snapshot
from unittest.mock import patch
from dataclasses import replace
import test_realtime_runtime as fixtures
from test_operator_advice import LocalPlanner
from go_duel_series import play,GO_MODEL,match_setup,ZERG_SCHEDULE,selected_schedule


class DuelSeriesTests(unittest.TestCase):
    def test_explicit_review_skip_preserves_local_evidence_and_spends_no_review_attempts(self):
        import go_duel_series as runner
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'logs').mkdir()
            def fake_play(folder,*args,**kwargs):
                folder.mkdir()
                players=[]
                for i in (1,2):
                    path=folder/f'p{i}'/'events.jsonl';path.parent.mkdir();path.write_text('')
                    players.append(dict(player=i,journal=str(path)))
                return dict(outcomes={},termination='local_fixture',result_source='unverified',players=players)
            with patch.object(runner,'ROOT',root),patch.object(runner,'play',side_effect=fake_play),\
                 patch.object(runner,'match_diagnostics',return_value=dict(errors=[],timeline=[])),\
                 patch.object(runner,'review_game',side_effect=AssertionError('external review forbidden')) as review,\
                 patch('sys.argv',['go_duel_series.py','--random-races','--games','1','--skip-model-reviews']):
                self.assertEqual(runner.main(),0)
            review.assert_not_called()
            current=json.loads((root/'logs/go-duels/current.json').read_text())
            folder=Path(current['series']);state=json.loads((folder/'series.json').read_text())
            self.assertTrue(state['skip_model_reviews']);self.assertTrue(state['matches'][0]['model_reviews_skipped'])
            self.assertEqual(state['budget']['attempted'],0)
            for i in (1,2):self.assertTrue((folder/f'match-01/p{i}/diagnostics.json').is_file())
    def test_random_schedule_is_seeded_independent_and_saved_for_resume(self):
        import random
        rng=random.Random(29)
        expected=[[rng.choice(('terran','zerg','protoss')) for _ in range(2)] for _ in range(4)]
        schedule=selected_schedule(games=4,random_races=True,seed=29)
        self.assertEqual(schedule,expected)
        self.assertEqual(selected_schedule(games=4,random_races=True,seed=29),schedule)
        for kwargs in (dict(zerg_series=True),dict(races=['zerg','terran']),dict(games=0)):
            with self.assertRaises(ValueError):selected_schedule(random_races=True,**kwargs)
        state=dict(games=4,schedule=schedule,races=schedule[0],
                   strategies_by_player_race=[{r:'P%d %s'%(i,r) for r in ('terran','zerg','protoss')} for i in (1,2)])
        restored=json.loads(json.dumps(state))
        for n,pair in enumerate(schedule,1):
            self.assertEqual(match_setup(restored,n),(pair,['P1 '+pair[0],'P2 '+pair[1]]))

    def test_random_mirror_keeps_each_players_own_strategy(self):
        state=dict(games=1,schedule=[['zerg','zerg']],races=['zerg','zerg'],
                   strategies_by_player_race=[{'zerg':'P1 private'},{'zerg':'P2 private'}])
        self.assertEqual(match_setup(state,1),(['zerg','zerg'],['P1 private','P2 private']))

    def test_explicit_single_pvz_keeps_default_and_fixed_schedules(self):
        self.assertEqual(selected_schedule(races=['protoss','zerg'],games=1),[['protoss','zerg']])
        self.assertEqual(selected_schedule(),[['terran','protoss']]*3)
        self.assertEqual(selected_schedule(True),ZERG_SCHEDULE)
        for kw in (dict(zerg_series=True,games=1),dict(zerg_series=True,races=['protoss','zerg']),
                   dict(games=0),dict(games=5),dict(races=['protoss','invented'])):
            with self.subTest(kw=kw),self.assertRaises(ValueError):selected_schedule(**kw)

    def test_four_game_schedule_keeps_opponents_and_strategies_separate_on_resume(self):
        state=dict(games=4,races=ZERG_SCHEDULE[0],schedule=ZERG_SCHEDULE,
            strategies_by_race=dict(zerg='own zerg review',protoss='own protoss review',terran='own terran review'))
        restored=json.loads(json.dumps(state))
        for number,opponent in enumerate(('protoss','terran','protoss','terran'),1):
            races,strategies=match_setup(restored,number)
            self.assertEqual(races,['zerg',opponent])
            self.assertEqual(strategies,['own zerg review','own '+opponent+' review'])
        legacy=dict(games=3,races=['terran','protoss'],strategies=['old p1','old p2'])
        self.assertEqual(match_setup(legacy,3),(['terran','protoss'],['old p1','old p2']))

    def test_concurrent_player_attempts_are_durable_and_resume_without_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'budget.json';budget=DurableBudget(path,50)
            sides=[PlayerBudget(budget,i) for i in range(2)]
            def spend(i):
                for _ in range(24):sides[i].reserve('battle')
            with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(spend,[0,1]))
            sides[0].reserve('review');sides[1].reserve('review')
            expected=dict(limit=50,attempted=50,by_role={'battle_P1':24,'battle_P2':24,'review_P1':1,'review_P2':1})
            self.assertEqual(json.loads(path.read_text()),expected)
            self.assertEqual(DurableBudget(path,50).report(),expected)

    def test_invalid_counter_cannot_be_silently_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'budget.json';path.write_text(json.dumps(dict(limit=50,attempted=3,by_role={'battle_P1':2})))
            with self.assertRaisesRegex(ValueError,'no reset'):DurableBudget(path,50)

    def test_snapshot_only_uses_the_selected_player_observation(self):
        commander=SimpleNamespace(tasks=SimpleNamespace(summary=lambda:['own task']),
            last_decision={'reason':'own reason'},control_policy={'own':True})
        world=SimpleNamespace(summary=lambda:{'own':{'Stalker':3},'visible_enemy':{}})
        payload=player_snapshot(1,world,commander)
        self.assertEqual(payload['world']['own'],{'Stalker':3})
        self.assertNotIn('opponent_world',payload)

    def test_go_runner_publishes_live_chat_window_contract(self):
        class Arena(fixtures.Backend):
            def __init__(self,**kwargs):
                self.homes=[(30,30),(160,160)];self.initial=[replace(fixtures.world([]),race=race) for race in kwargs['races']]
                self.controllers=[SimpleNamespace(ping=lambda:SimpleNamespace(game_version='local test fixture'))]
                self.clock=SimpleNamespace(report=lambda:{'game_s':1,'wall_s':1})
                self.start_location_report=[];self.surrender_report=None;self.action_errors=0;self.ended=False
            def step(self,batches):self.ended=True;return self.initial
            def result(self):return {}
            def save_replay(self,path):path.write_bytes(b'local test fixture, not a real replay')
            def close(self):pass
        def client(model,path):
            c=LocalPlanner();c.model=GO_MODEL;c.request_status=lambda:{'state':'idle'};return c
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)/'match';budget=DurableBudget(Path(directory)/'budget.json',6)
            with patch('go_duel_series.RawArena',Arena),patch('go_duel_series.GoPlanner',side_effect=client),patch('go_duel_series.subprocess.Popen') as launch:
                launch.return_value.pid=123
                report=play(folder,1,['choose own strategy']*2,budget,dict(requests=2,wall_s=2,game_s=10,seed=7),True,races=['zerg','protoss'])
            self.assertEqual(launch.call_args.args[0][-2:],['--run',str(folder)])
            self.assertEqual(len(report['samples'][0]['commanders']),2)
            self.assertEqual(report['races'],['zerg','protoss'])
            self.assertEqual([p['race'] for p in report['players']],['zerg','protoss'])
            for i,c in enumerate(report['samples'][0]['commanders']):
                self.assertEqual(c['operator_advice']['player'],i+1)
                self.assertTrue(Path(c['feedback']['journal']).is_file())
            self.assertFalse(report['user_intervention']);self.assertTrue(report['cleanup_complete'])


if __name__=='__main__':unittest.main()
