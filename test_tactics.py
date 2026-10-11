"""Deterministic tactical safety/ownership scenarios, with no API or game launch."""
import json
import math
import tempfile
import time
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from arena.commander import Commander
from arena.deepseek_planner import DeepSeekFlashPlanner
from arena.raw_engine import ATTACK, MOVE, command
from arena.tactics import ArmyTactics
from arena.tasks import TaskQueue
from test_realtime_runtime import Backend, unit, world


class Grid:
    def free(self,x,y):return 0<=x<200 and 0<=y<200
    def route(self,start,end):
        n=max(1,math.ceil(math.dist(start,end)))
        return [(int(start[0]+(end[0]-start[0])*i/n),int(start[1]+(end[1]-start[1])*i/n)) for i in range(n+1)]


class Engine(Backend):
    def path_distances(self,index,start,points):return [math.dist(start,p) for p in points]


class Idle:
    def ready_for_request(self):return False
    def chat(self,*a):raise AssertionError('no API calls expected')


def roach(tag,x=22,y=30,**kw):
    return unit(tag,110,x,y,ground_dps=10,attack_range=4,**kw)


class TacticsTests(unittest.TestCase):
    def setUp(self):
        self.b=Engine();self.events=[];self.clock=100.
        self.rally=SimpleNamespace(grid=Grid())
        self.t=ArmyTactics(self.b,0,self.rally,lambda kind,**kw:self.events.append((kind,kw)),lambda _:None,clock=lambda:self.clock)
        self.addCleanup(self.t.close)
        self.t.anchor=(22,30);self.t.next_scan=1000
        self.base=unit(1,86,is_building=True)
    def w(self,units=(),sec=1):return replace(world([self.base,*units],sec),race='zerg')
    def orders(self,actions):return {a.action_raw.unit_command.unit_tags[0]:a.action_raw.unit_command for a in actions}

    def test_new_unit_with_native_move_is_adopted_into_attack_move_assembly(self):
        orders=self.orders(self.t.tick(self.w([roach(2,30,30,orders=(MOVE,))])))
        self.assertEqual(orders[2].ability_id,ATTACK)
        self.assertEqual(orders[2].target_world_space_pos.x,22)
        self.assertEqual(self.t.fighting,set())

    def test_newborn_reinforcements_wait_for_a_full_batch(self):
        self.t.mission('move_out');army=[roach(i) for i in range(2,8)]
        orders=self.orders(self.t.tick(self.w(army)))
        self.assertEqual(self.t.fighting,set(range(2,8)))
        self.assertEqual(orders[2].target_world_space_pos.x,160)
        army=[replace(u,x=80,y=80,orders=(ATTACK,)) for u in army]
        orders=self.orders(self.t.tick(self.w([*army,roach(9)],2)))
        self.assertNotIn(9,self.t.fighting)
        self.assertEqual(orders[9].target_world_space_pos.x,22)
        self.t.tick(self.w([*army,*[roach(i) for i in range(9,15)]],3))
        self.assertTrue(set(range(9,15))<=self.t.fighting)

    def test_distributed_army_does_not_count_as_assembled(self):
        self.t.mission('move_out')
        self.t.tick(self.w([roach(i,60+i,60) for i in range(2,12)]))
        self.assertFalse(self.t.fighting)

    def test_protected_workers_transports_and_unfinished_units_are_excluded(self):
        army=[roach(2),roach(3),unit(4,104,ground_dps=5),roach(5,cargo_taken=2),roach(6,progress=.5)]
        orders=self.orders(self.t.tick(self.w(army),{2}))
        self.assertEqual(set(orders),{3})

    def test_custom_owner_takeover_clears_tactical_group(self):
        self.t.mission('move_out');army=[roach(i) for i in range(2,8)]
        self.t.tick(self.w(army))
        self.assertFalse(self.t.tick(self.w(army,2),set(range(2,8))))
        self.assertFalse(self.t.tags);self.assertFalse(self.t.fighting)
        orders=self.orders(self.t.tick(self.w(army,3)))
        self.assertTrue(orders)

    def test_safe_endpoint_with_dangerous_path_stops_old_rally(self):
        enemy=unit(99,32,55,30,4,ground_dps=40,attack_range=13)
        self.t._remember(self.w([enemy]))
        self.assertEqual(self.t._risk(self.t.anchor),0)
        self.assertFalse(self.t._safe_route((85,30),self.t.anchor))
        orders=self.orders(self.t.tick(self.w([roach(2,85,30,orders=(MOVE,)),enemy])))
        self.assertEqual(orders[2].ability_id,4)

    def test_air_route_uses_anti_air_threats(self):
        ground=unit(90,32,55,30,4,ground_dps=40,attack_range=13)
        self.t._remember(self.w([ground]))
        self.assertTrue(self.t._safe_route((85,30),self.t.anchor,True))
        self.t._remember(self.w([unit(91,48,55,30,4,air_dps=10,attack_range=6)]))
        self.assertFalse(self.t._safe_route((85,30),self.t.anchor,True))

    def test_known_tank_threat_is_not_forgotten_on_next_fog_frame(self):
        enemy=unit(99,32,55,30,4,ground_dps=40,attack_range=13)
        self.t._remember(self.w([enemy]))
        self.t._remember(self.w([],3))
        self.assertFalse(self.t._safe_route((85,30),self.t.anchor))
        self.t._remember(replace(self.w([],4),dead_units=(99,)))
        self.assertTrue(self.t._safe_route((85,30),self.t.anchor))

    def test_overwhelming_contact_withdraws_instead_of_attacking_tanks(self):
        enemy=unit(99,32,40,30,4,ground_dps=100,attack_range=13)
        self.t.mission('move_out')
        orders=self.orders(self.t.tick(self.w([roach(2,30,30),enemy])))
        self.assertEqual(orders[2].ability_id,MOVE)
        self.assertGreater(self.t.withdraw_until,1)
        self.assertFalse(self.t.fighting)

    def test_unsafe_anchor_is_relocated_behind_base(self):
        self.t.anchor=(42,30)
        enemy=unit(99,32,48,30,4,ground_dps=60,attack_range=13)
        self.t.tick(self.w([roach(2),enemy]))
        self.assertIsNotNone(self.t.anchor)
        self.assertEqual(self.t._risk(self.t.anchor),0)
        self.assertLess(self.t.anchor[0],42)

    def test_no_navigation_or_base_stops_native_rally(self):
        self.rally.grid=None;self.t.anchor=None;self.t.next_scan=0
        orders=self.orders(self.t.tick(self.w([roach(2)])))
        self.assertEqual(orders[2].ability_id,4)
        self.assertIsNone(self.t.anchor)

    def test_hold_stays_at_anchor_and_does_not_resume_attack(self):
        self.t.mode='hold'
        orders=self.orders(self.t.tick(self.w([roach(2)])))
        self.assertEqual(orders[2].ability_id,18)

    def test_sieged_tank_requires_the_stance_executor(self):
        tank=unit(2,32,ground_dps=30)
        self.assertFalse(self.t._eligible(tank))
        self.t.combat_micro=True
        self.assertTrue(self.t._eligible(tank))

    def test_one_threatened_reinforcement_does_not_recall_a_distant_army(self):
        self.t.mission('move_out');self.t.fighting=set(range(2,8))
        army=[roach(i,110,110) for i in range(2,8)]
        enemy=unit(99,32,44,30,4,ground_dps=100,attack_range=13)
        self.t.tick(self.w([*army,roach(9,30,30),enemy]))
        self.assertEqual(self.t.fighting,set(range(2,8)))

    def test_escape_cannot_move_deeper_into_initial_threat(self):
        enemy=unit(99,32,40,30,4,ground_dps=100,attack_range=13)
        self.t._remember(self.w([enemy]))
        self.assertTrue(self.t._safe_route((30,30),(18,30),escape=True))
        self.assertFalse(self.t._safe_route((30,30),(65,30),escape=True))

    def test_spatial_threat_index_matches_full_scan_at_cell_boundaries(self):
        foes=[unit(91,32,16,32,4,ground_dps=30,attack_range=13),
              unit(92,48,31.9,16.1,4,ground_dps=7,air_dps=7,attack_range=6),
              unit(93,48,-1,4,4,ground_dps=3,attack_range=3)]
        self.t._remember(self.w(foes))
        for flying in (False,True):
            for x in (-2,0,8,15.9,16,16.1,24,31.9,32,32.1,48,64):
                for y in (0,8,16,24,32,48):
                    expected=sum((u.air_dps if flying else u.ground_dps) for u in foes
                                 if u.distance((x,y))<max(9,u.attack_range+u.radius+3))
                    self.assertEqual(self.t._risk((x,y),flying),expected)

    def test_same_loop_and_active_order_are_not_spammed(self):
        w=self.w([roach(2,orders=(ATTACK,))]);self.assertTrue(self.t.tick(w))
        self.assertFalse(self.t.tick(w))
        self.assertFalse(self.t.tick(self.w([roach(2,orders=(ATTACK,))],2)))

    def reply(self,w,plan=None,**kw):
        own=[u for u in w.my_units if self.t._eligible(u)]
        row=dict(response=json.dumps(plan or dict(mode='attack',objective='enemy_start',min_group=8,reason='集结后出击')),
                 error=None,generation=self.t.generation,signature=self.t._signature(w,own),wall=self.clock,game_s=w.seconds)
        row.update(kw);self.t.outbox.put(row)
        self.t._receive(w,self.t._signature(w,own))

    def test_fresh_tactical_plan_is_accepted(self):
        self.reply(self.w([roach(2)]))
        self.assertEqual(self.t.mode,'attack');self.assertEqual(self.t.min_group,8)

    def test_late_or_wrong_generation_reply_cannot_override_current_mission(self):
        w=self.w([roach(2)])
        self.reply(w,wall=self.clock-7)
        self.reply(w,generation=-1)
        self.reply(w,signature=('old owner',))
        self.assertEqual(self.t.mode,'assemble');self.assertEqual(self.t.expired,3)

    def test_invalid_model_control_does_not_mutate_mode(self):
        self.reply(self.w([roach(2)]),dict(mode='attack',objective='enemy_start',min_group=True,reason='错误'))
        self.assertEqual(self.t.errors,1);self.assertEqual(self.t.mode,'assemble')

    def test_persistent_mission_is_accepted_even_before_units_are_born(self):
        q=TaskQueue(self.b,0,lambda _:None);q.army_tactics=self.t
        q.enqueue([dict(skill='move_out')],0)
        self.assertEqual(q.tick(self.w(),set()),[])
        self.assertEqual(q.tasks[0].status,'completed');self.assertEqual(self.t.objective,'enemy_start')

    def test_slow_model_does_not_block_local_new_unit_orders(self):
        import threading
        gate=threading.Event()
        class Slow:
            def chat(self,*a):gate.wait(.5);return '{"mode":"hold","objective":"defend_base","min_group":6,"reason":"守住"}'
        t=ArmyTactics(self.b,0,self.rally,lambda *a,**k:None,lambda _:None,Slow())
        self.addCleanup(t.close);self.addCleanup(gate.set)
        t.anchor=(22,30);t.next_scan=1000
        start=time.monotonic();orders=t.tick(self.w([roach(2)]))
        self.assertTrue(orders);self.assertLess(time.monotonic()-start,.15)
        self.assertTrue(t.thinking)


class IntegrationTests(unittest.TestCase):
    def test_commander_has_one_owner_and_releases_threads(self):
        b=Engine();w=world([unit(1,86,is_building=True),roach(2)])
        b.initial=[w]
        with tempfile.TemporaryDirectory() as folder:
            c=Commander(b,0,Idle(),opening=False,tactics_enabled=True,feedback_dir=folder)
            try:
                c.rally.grid=Grid();c.tactics.anchor=(22,30);c.tactics.next_scan=1000
                c.tasks.enqueue([dict(skill='move_out')],0)
                batch=c.tick(w)
                self.assertEqual(sum(2 in a.action_raw.unit_command.unit_tags for a in batch),1)
                self.assertEqual(c.stats()['army_tactics']['objective'],'enemy_start')
                self.assertIn('Army tactics is ON',c.system)
            finally:c.stop()
            self.assertFalse(c.worker.is_alive())

    def test_conflicting_ownership_is_rejected_before_threads_start(self):
        with self.assertRaises(ValueError):Commander(Engine(),0,Idle(),tactics_enabled=True,defense_client=Idle())
        with self.assertRaises(ValueError):Commander(Engine(),0,Idle(),tactics_enabled=True,combat_reflex=True)

    def test_high_economy_and_non_thinking_tactics_request_bodies(self):
        for client,expected in ((DeepSeekFlashPlanner(reasoning_effort='high'),'enabled'),(DeepSeekFlashPlanner(defense=True),'disabled')):
            requests=[]
            def post(url,body,headers):
                requests.append(body)
                return dict(model='deepseek-flash',choices=[dict(finish_reason='stop',message=dict(content='{}'))])
            client._post=post;client._call_openai('system','{}')
            self.assertEqual(requests[0]['thinking']['type'],expected)
            if expected=='enabled':
                self.assertEqual(requests[0]['reasoning_effort'],'high')
                # high reasoning needs output headroom: 6000 was fully consumed by
                # reasoning_tokens and the visible JSON never started (finish_reason=length).
                self.assertEqual(requests[0]['max_tokens'],8000)
                self.assertGreater(requests[0]['max_tokens'],DeepSeekFlashPlanner(defense=True).max_tokens)
                self.assertLess(requests[0]['max_tokens'],DeepSeekFlashPlanner(developer=True).max_tokens)
            else:
                self.assertNotIn('reasoning_effort',requests[0])
                self.assertEqual(requests[0]['max_tokens'],384)

    def test_tactical_feed_does_not_complete_an_economy_request(self):
        from llm_output_window import Feed
        feed=Feed();feed.consume(dict(record='request_queued',request_id=1,prompt={}))
        feed.consume(dict(record='tactical_request_finished',request_id=1,latency_s=.2,
                          response='{"mode":"assemble","objective":"defend_base","min_group":6,"reason":"先集结"}'))
        self.assertEqual(feed.pending,1);self.assertIn('先集结',feed.entries[-1]['body'])


if __name__=='__main__':unittest.main()
