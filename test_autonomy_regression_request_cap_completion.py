"""A call cap suppresses new calls; issued actions keep their original time budget."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
import autonomous_bot as runner
from arena.autonomous_bot import RequestBudget
from test_realtime_runtime import unit,world,CC


class RequestCapCompletion(unittest.TestCase):
    def match(self,victory_at,game_limit=600):
        class Base:
            calls=attempts=24;closed=False;timeout=35
            def close(self):self.closed=True
        class Arena:
            ended=False;surrender_report=None;action_errors=0;pending=False
            def __init__(self,*args,**kwargs):
                self.current=world([unit(1,CC,is_building=True),unit(2,48,ground_dps=10)],game_limit-20)
                self.initial=[self.current]
                self.controllers=[SimpleNamespace(ping=lambda:SimpleNamespace(game_version='test-fixture'))]
            def step(self,actions):
                if self.pending:
                    self.ended=True;return [self.current]
                self.current=replace(self.current,game_loop=self.current.game_loop+112)
                if victory_at is not None and self.current.seconds>=victory_at:self.ended=True
                return [self.current]
            def result(self):return {1:'Defeat' if self.pending else 'Victory'} if self.ended else {}
            def request_surrender(self,index,reason):
                self.pending=True;self.surrender_report={'result_source':'acknowledged_concession_rule'}
            def save_replay(self,path):path.write_bytes(b'unit-test fixture, not a real replay')
            def close(self):pass
        class Commander:
            client_fatal=None;thinking=False;control_policy={};last_decision=None
            def __init__(self,arena,index,client,**kwargs):
                self.client=client;self.tasks=SimpleNamespace(summary=lambda:[])
                self.feedback=SimpleNamespace(path='unit-test-fixture')
            def tick(self,observation):
                if self.client.ready_for_request():raise AssertionError('Call cap must prevent additional model turns')
                return []
            def ingest_feedback(self,observation):pass
            def record_feedback(self,*args,**kwargs):pass
            def stop(self):pass
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)/'match';budget=RequestBudget(208);budget.used=24
            with patch.object(runner,'ChatGPTSubscriptionPlanner',Base), \
                 patch.object(runner,'RawArena',Arena),patch.object(runner,'Commander',Commander), \
                 patch.object(runner.time,'sleep',lambda seconds:None):
                # Base has no authentication or network operation.
                with patch.object(Base,'__init__',lambda self,*args,**kwargs:None):
                    report=runner.run_match(Path(directory),folder,'chatgpt/gpt-6-luna','easy',
                        'fixture',budget,SimpleNamespace(max_requests=24,max_game_seconds=game_limit,
                        max_wall_seconds=game_limit+60,seed=7),1)
            self.assertEqual(budget.used,24)
            self.assertEqual(report['model_calls'],24)
            self.assertTrue(report['cleanup_complete'])
            return report

    def test_issued_attack_can_finish_before_unchanged_time_limit(self):
        report=self.match(victory_at=590)
        self.assertEqual(report['outcome'],'Victory')
        self.assertEqual(report['result_source'],'engine_player_result')
        self.assertEqual(report['termination'],'engine_end')

    def test_call_cap_cannot_extend_match_time_forever(self):
        report=self.match(victory_at=None)
        self.assertEqual(report['final_world']['game_s'],600)
        self.assertEqual(report['outcome'],'Defeat')
        self.assertEqual(report['termination'],'time_budget')

    def test_thirty_minute_deadline_is_enforced_without_extra_calls(self):
        report=self.match(victory_at=None,game_limit=1800)
        self.assertEqual(report['final_world']['game_s'],1800)
        self.assertEqual(report['termination'],'time_budget')
        self.assertEqual(report['model_attempts'],24)
