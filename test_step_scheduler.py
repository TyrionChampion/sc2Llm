"""Step synchronization, bounded request alignment and actual mailbox concurrency.

Controlled observations test invariants, not native combat quality or speedup.
"""
import json
import math
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from types import SimpleNamespace as S
from unittest.mock import patch

from arena.commander import Commander
from arena.raw_engine import ATTACK, MOVE
from arena.step_scheduler import AdaptiveSteps, PlannerBatcher
from test_rally_orders import engine_backend
from test_realtime_runtime import unit, world


class AdaptiveStepTests(unittest.TestCase):
    def setUp(self):self.policy=AdaptiveSteps()

    def test_quiet_movement_and_contact_select_distinct_steps(self):
        army=unit(10,48,30,30,ground_dps=7,attack_range=6)
        self.assertEqual(self.policy.choose([world([army],20)]),64)
        self.assertEqual(self.policy.choose([world([replace(army,orders=(ATTACK,))],21)]),32)
        enemy=unit(99,48,40,30,alliance=4,ground_dps=7,attack_range=6)
        self.assertEqual(self.policy.choose([world([army,enemy],22)]),8)
        self.assertEqual(self.policy.choose([world([army],23)]),8)
        self.assertEqual(self.policy.choose([world([army],25)]),64)

    def test_either_players_contact_requires_common_small_step(self):
        mine=unit(10,48,30,30,ground_dps=7)
        enemy=unit(99,48,35,30,alliance=4,ground_dps=7)
        self.assertEqual(self.policy.choose([world([],20),world([mine,enemy],20)]),8)

    def test_damage_or_observed_loss_shrinks_step_without_visible_enemy(self):
        mine=unit(10,48,30,30,ground_dps=7)
        self.policy.choose([world([mine],20)])
        self.assertEqual(self.policy.choose([world([replace(mine,health=30)],21)]),8)
        self.assertEqual(self.policy.choose([replace(world([],24),dead_units=(10,))]),8)

    def test_unobserved_enemy_state_is_not_an_input_and_current_effects_shrink_step(self):
        w=world([],20)
        self.assertEqual(self.policy.choose([w]),64)
        self.assertEqual(self.policy.choose([replace(w,game_loop=w.game_loop+8,effects=((1,4,30,30,2),))]),8)

    def test_next_decision_deadline_clips_step_to_exact_engine_loop(self):
        w=replace(world([],0),game_loop=448)
        count=self.policy.choose([w],next_deadline=(w.game_loop+3)/22.4)
        self.assertEqual(count,3)
        self.assertEqual(w.game_loop+count,451)

    def test_invalid_sizes_rejected(self):
        for sizes in ((0,64),(8,225),(64,32),(True,64)):
            with self.subTest(sizes=sizes),self.assertRaises(ValueError):AdaptiveSteps(*sizes)


class DuePlanner:
    def __init__(self,due):
        self.due=due;self.calls=[];self.tactics=None;self.control_policy={}
        self.surrender=None;self.client_fatal=None;self.backend=S(match_control_pending=False)
        self.urgent_request_game_s=float('inf')
    def request_deadline(self):return self.due
    def request_plan(self,w,due_at=None):
        self.calls.append((w.game_loop,due_at));self.due=float('inf');return True


class BatchDeadlineTests(unittest.TestCase):
    def w(self,loop):return replace(world([],0),game_loop=loop)

    def test_nearby_deadlines_are_collected_at_same_epoch(self):
        players=[DuePlanner(1.1),DuePlanner(1.8)];batcher=PlannerBatcher(players)
        batcher.dispatch([self.w(44),self.w(44)])
        self.assertFalse(any(p.calls for p in players))
        self.assertEqual(batcher.next_deadline([self.w(44)]*2),45/22.4)
        batcher.dispatch([self.w(45)]*2)
        self.assertEqual([p.calls[0][0] for p in players],[45,45])
        self.assertIs(players[0].request_gate,players[1].request_gate)
        self.assertTrue(players[0].request_gate.is_set())
        self.assertEqual(batcher.public()['largest_batch'],2)

    def test_urgent_deadline_bypasses_epoch_alignment(self):
        planner=DuePlanner(1.4);planner.urgent_request_game_s=1.5
        batcher=PlannerBatcher([planner]);batcher.dispatch([self.w(34)])
        self.assertEqual(planner.calls[0][0],34)

    def test_common_clock_keeps_one_ahead_player_from_starting_early(self):
        players=[DuePlanner(1.1),DuePlanner(1.8)];batcher=PlannerBatcher(players)
        batcher.dispatch([self.w(45),self.w(44)])
        self.assertFalse(any(p.calls for p in players))

    def test_one_loop_clock_offset_does_not_split_later_batches_or_add_epoch_drift(self):
        players=[DuePlanner(0),DuePlanner(0)];batcher=PlannerBatcher(players)
        # 26 seconds lies between loops: first common epoch is loop 583.
        first=[self.w(584),self.w(583)];batcher.dispatch(first)
        for p,w in zip(players,first):p.due=w.seconds+20
        later=[self.w(1032),self.w(1031)];batcher.dispatch(later)
        self.assertEqual([len(p.calls) for p in players],[2,2])
        self.assertEqual([p.calls[1][0]-p.calls[0][0] for p in players],[448,448])
        self.assertEqual(batcher.public()['batches'],2)

    def test_unavailable_overdue_client_does_not_force_tiny_steps(self):
        planner=DuePlanner(1);planner.request_plan=lambda *a,**kw:False
        batcher=PlannerBatcher([planner]);batcher.dispatch([self.w(100)])
        self.assertEqual(batcher.next_deadline([self.w(100)]),float('inf'))
        self.assertEqual(batcher.batches,0)

    def test_failed_prompt_preparation_releases_previously_staged_worker(self):
        players=[DuePlanner(0),DuePlanner(0)];batcher=PlannerBatcher(players)
        def fail(*a,**kw):raise RuntimeError('prompt preparation failed')
        players[1].request_plan=fail
        with self.assertRaisesRegex(RuntimeError,'prompt preparation'):batcher.dispatch([self.w(0)]*2)
        self.assertTrue(players[0].request_gate.is_set())

    def test_conceding_player_does_not_start_any_more_planners(self):
        planner=DuePlanner(0);planner.surrender={'state':'requested'}
        PlannerBatcher([planner]).dispatch([self.w(100)])
        self.assertFalse(planner.calls)


class RecordedDeadlineTests(unittest.TestCase):
    def test_real_duel_deadlines_have_bounded_alignment_and_urgent_bypass(self):
        from verify_ai_duel_scheduling import EVIDENCE
        evidence=json.loads(EVIDENCE.read_text())['players']
        aligner=PlannerBatcher([])
        for data in evidence.values():
            for sent in data['requests']:
                due=sent+20
                aligned=aligner._aligned(due)
                self.assertGreaterEqual(aligned+1e-9,due)
                self.assertLessEqual(aligned-due,2+1/22.4)
                before=replace(world([],0),game_loop=int(due*22.4))
                if aligned>before.seconds:
                    count=AdaptiveSteps().choose([before],next_deadline=aligned)
                    self.assertLessEqual(before.game_loop+count,round(aligned*22.4))
            for event in data['events']:
                if event['kind']!='tactical_base_alert':continue
                due=event['game_s']+2
                planner=DuePlanner(due);planner.urgent_request_game_s=due
                batcher=PlannerBatcher([planner]);loop=math.ceil(due*22.4)
                batcher.dispatch([replace(world([],0),game_loop=loop)])
                self.assertTrue(planner.calls)
                self.assertLessEqual(planner.calls[0][0]/22.4-event['game_s'],2+1/22.4+1e-9)


class MailboxConcurrencyTests(unittest.TestCase):
    def test_both_main_and_battle_workers_start_without_waiting_for_each_other(self):
        barrier=threading.Barrier(4);prompts={};clients=[];commanders=[];worlds=[]
        class Client:
            def __init__(self,key,reply):self.key=key;self.reply=reply
            def chat(self,system,user):
                prompts[self.key]=json.loads(user)
                barrier.wait(timeout=3)
                return json.dumps(self.reply)
        with tempfile.TemporaryDirectory() as directory:
            try:
                for i in (0,1):
                    _,backend=engine_backend('terran',(ATTACK,MOVE,3673));backend.realtime=False
                    w=world([unit(1+i*100,18,is_building=True),unit(10+i*100,48,35,35,ground_dps=7)],20)
                    backend.initial=[w];backend.register_action_context=lambda *a:None
                    main=Client((i,'main'),{'tasks':[],'reason':'保持运营'})
                    battle=Client((i,'battle'),{'orders':[],'reason':'保持战术'})
                    c=Commander(backend,0,main,opening=False,tactics_enabled=True,tactical_client=battle,
                        feedback_dir=directory,display_player=i+1)
                    c.tactics.mission('move_out');commanders.append(c);worlds.append(w)
                batcher=PlannerBatcher(commanders)
                for c,w in zip(commanders,worlds):c.tick(w)
                self.assertFalse(prompts)  # Executors can update before any model is called.
                batcher.dispatch(worlds)
                end=time.monotonic()+4
                while any(c.outbox.empty() or c.tactics.outbox.empty() for c in commanders) and time.monotonic()<end:time.sleep(.002)
                self.assertEqual(set(prompts),{(i,role) for i in (0,1) for role in ('main','battle')})
                self.assertEqual(batcher.public()['largest_batch'],4)
                for i,(c,w) in enumerate(zip(commanders,worlds)):
                    self.assertEqual(prompts[i,'main']['world'],w.summary())
                    self.assertEqual(prompts[i,'battle']['squads'][0]['id'],'squad_'+str(10+i*100))
                    self.assertIsNone(c.outbox.get_nowait()[2])
                    self.assertIsNone(c.tactics.outbox.get_nowait()['error'])
            finally:
                for c in commanders:c.stop()

    def test_native_main_uses_batching_and_adaptive_steps_and_saves_metrics(self):
        import native_llm
        from pathlib import Path
        from test_native_step_mode import NativeStepTests
        arena=NativeStepTests().engine(count=2)
        arena.controllers[0].loop+=1
        arena.last=[c.observe() for c in arena.controllers]
        arena.size=(200,200);arena.enemy_start=lambda index:(160,160) if index==0 else (30,30)
        arena.homes=[(30,30),(160,160)];arena.races=['terran','terran'];arena.tech_tree=None
        arena.initial=arena.snapshots();arena.version='fixture';arena.start_location_report={}
        arena.closed=False;arena.surrender_report=None
        arena.close=lambda:setattr(arena,'closed',True)
        arena.save_replay=lambda p:Path(p).write_bytes(b'controlled fixture replay')
        original_step=arena.step
        def step(*args,**kwargs):
            result=original_step(*args,**kwargs)
            if arena.stepped_loops>=1200:arena._terminal_result={1:'Tie',2:'Tie'}
            return result
        arena.step=step
        barrier=threading.Barrier(2)
        class Client:
            calls=0
            def chat(self,*args):
                self.calls+=1;barrier.wait(timeout=3);return '{"tasks":[],"reason":"保持运营"}'
        class StrategicCommander(Commander):
            def __init__(self,*args,**kwargs):super().__init__(*args,interval=20,**kwargs)
        with tempfile.TemporaryDirectory() as directory:
            arena.directory=directory;target=Path(directory)/'match.json'
            clients=[Client(),Client()]
            players=[(client,dict(side='P'+str(i+1),provider='fixture',model='fixture',key_source='none')) for i,client in enumerate(clients)]
            with patch.object(native_llm,'NativeArena',return_value=arena),patch.object(native_llm,'Commander',StrategicCommander):
                native_llm.main(players_override=players,catalogs_override=[None,None],opening=False,
                    argv=['--no-realtime','--headless','--no-overlay','--seconds','4',
                          '--feedback-dir',str(Path(directory)/'feedback'),'--output',str(target)])
            report=json.loads(target.read_text())
            self.assertEqual(report['outcome'],{'1':'Tie','2':'Tie'})
            self.assertEqual(report['timing']['planner_batches']['largest_batch'],2)
            self.assertEqual([c['decisions'] for c in report['commanders']],[3,3])
            queued=[]
            for c in report['commanders']:
                records=[json.loads(line) for line in Path(c['feedback']['journal']).read_text().splitlines()]
                loops=[r['game_loop'] for r in records if r.get('record')=='request_queued']
                self.assertEqual(loops[2]-loops[1],448)  # 20 seconds, no repeated extra epoch.
                queued.append(loops)
            self.assertEqual([a-b for a,b in zip(*queued)],[1,1,1])
            self.assertTrue(report['adaptive_step']);self.assertIn('64',report['timing']['step_counts'])
            self.assertEqual(arena.controllers[0].steps,arena.controllers[1].steps)
            self.assertFalse(report['timing']['advance_wall_throttled'])
            self.assertEqual(report['cleanup_status'],'complete');self.assertTrue(arena.closed)


if __name__=='__main__':unittest.main()
