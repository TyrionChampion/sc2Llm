"""Non-realtime series accounting and the real bounded Go prompt path; no API."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from arena.autonomous_bot import BotPlanner, RequestBudget, INITIAL_STRATEGY
from arena.linux_step import LinuxStepArena
from arena.native_engine import NativeArena
from go_duel_series import DurableBudget, PlayerBudget
from go_step_series import SCHEDULE


class StepSeriesTests(unittest.TestCase):
    def test_linux_engine_reuses_the_tested_native_pause_and_step_executor(self):
        self.assertIs(LinuxStepArena.step,NativeArena.step)
        self.assertIs(LinuxStepArena.timing,NativeArena.timing)
        for kwargs in (dict(races=['terran']),dict(races=['unknown','zerg']),dict(step_loops=True),dict(step_loops=225)):
            with self.subTest(kwargs=kwargs),self.assertRaises(ValueError):LinuxStepArena(**kwargs)

    def test_four_game_schedule_covers_three_races_and_all_cross_matchups(self):
        self.assertEqual(len(SCHEDULE),4)
        self.assertEqual({frozenset(r) for r in SCHEDULE},
                         {frozenset(r) for r in [('terran','protoss'),('protoss','zerg'),('zerg','terran')]})

    def test_new_step_budget_is_independent_and_resume_preserves_attempts(self):
        with tempfile.TemporaryDirectory() as root:
            old=Path(root)/'old.json';old.write_text('{"attempted":181}')
            path=Path(root)/'budget.json';budget=DurableBudget(path,512)
            PlayerBudget(budget,0).reserve('battle');PlayerBudget(budget,1).reserve('battle')
            restored=DurableBudget(path,512)
            self.assertEqual(restored.report()['attempted'],2)
            self.assertEqual(restored.report()['by_role'],{'battle_P1':1,'battle_P2':1})
            self.assertEqual(old.read_text(),'{"attempted":181}')

    def test_running_series_cannot_be_silently_restarted(self):
        import go_step_series as runner
        with tempfile.TemporaryDirectory() as root:
            folder=Path(root)/'logs/go-step-duels/four-test';folder.mkdir(parents=True)
            state=folder/'series.json';state.write_text(json.dumps(dict(state='running')))
            original=state.read_bytes()
            with patch.object(runner,'ROOT',Path(root)),patch('sys.argv',['go_step_series.py','--resume',str(folder)]):
                with self.assertRaisesRegex(ValueError,'silently replayed'):runner.main()
            self.assertEqual(state.read_bytes(),original)

    def test_bounded_go_wrapper_keeps_new_offensive_tool_contract(self):
        class Capture:
            timeout=60;closed=False;calls=0;attempts=0;last_metadata={}
            model='opencode-go/deepseek-v4.1-flash'
            def chat(self,system,user):
                self.system=system;self.payload=json.loads(user)
                self.calls+=1;self.attempts+=1
                return json.dumps(dict(tasks=[],reason='读取新工具说明'))
        client=Capture();planner=BotPlanner(client,RequestBudget(8),INITIAL_STRATEGY)
        planner.chat('main system is intentionally replaced',json.dumps(dict(world=dict(game_s=0,race='terran',own={}),
            offensive_operations=dict(enabled=True,tasks=['raid','drop_raid']),control_policy=dict(spatial_missions_available=True))))
        self.assertIn('drop_raid',client.system)
        self.assertIn('retreat_enemy_count',client.system)
        self.assertIn('retreat_target',client.system)
        self.assertEqual(client.payload['offensive_operations']['tasks'],['raid','drop_raid'])

    def test_explicit_planner_factory_keeps_both_requested_model_identities(self):
        import go_step_series as runner
        seen=[]
        class Client:
            timeout=60;closed=False;calls=0;attempts=0
            max_battle_context_chars=90000
            def __init__(self,model,directory):
                self.model=model;seen.append((model,directory.name))
            def close(self):self.closed=True
        models=['chatgpt/gpt-6.1-sol','chatgpt/gpt-6.1-sol']
        state=dict(schedule=[['protoss','zerg']],models=models,seed=7,
                   limits=dict(requests=64,wall_s=2400,game_s=1800,model_timeout_s=120))
        with tempfile.TemporaryDirectory() as root:
            folder=Path(root)
            budget=DurableBudget(folder/'budget.json',128)
            with patch.object(runner,'LinuxStepArena',side_effect=RuntimeError('synthetic startup interruption')):
                result=runner.play(folder/'match-01',1,state,budget,planner_factory=Client)
            self.assertEqual(seen,[(models[0],'planner'),(models[1],'planner')])
            self.assertEqual(result['models'],models)
            self.assertEqual(result['races'],['protoss','zerg'])
            self.assertEqual(result['termination'],'environment_error')
            self.assertEqual(result['outcomes'],{})
            self.assertTrue(result['cleanup_complete'])
            self.assertEqual(budget.used,0)

    def test_invalid_model_pair_is_rejected_before_creating_match_or_calling_provider(self):
        import go_step_series as runner
        with tempfile.TemporaryDirectory() as root:
            folder=Path(root);budget=DurableBudget(folder/'budget.json',128)
            for value in (None,'ab',[],['chatgpt/gpt-6.1-sol'],['', 'chatgpt/gpt-6.1-sol']):
                with self.subTest(models=value),self.assertRaises(ValueError):
                    runner.play(folder/'match-01',1,dict(models=value),budget,
                                planner_factory=lambda *a:self.fail('provider must not be called'))
                self.assertFalse((folder/'match-01').exists())
            self.assertEqual(budget.used,0)


if __name__=='__main__':unittest.main()
