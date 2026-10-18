"""Explicit whole-army coverage across bases, old guards and new reinforcements."""
import unittest
import json
import tempfile
import time
from dataclasses import replace
from types import SimpleNamespace

from arena.battle_agent import BattleAgent
from arena.battle_plans import output_schema
from arena.raw_engine import ATTACK, MOVE
from arena.tasks import TaskQueue,PlanValidationError
from arena.commander import Commander
from test_battle_agent import Idle
from test_rally_orders import engine_backend
from test_realtime_runtime import unit,world


class AllArmyAttackTests(unittest.TestCase):
    def setUp(self):
        _,self.b=engine_backend('terran',(ATTACK,3673,MOVE))
        self.events=[]
        self.q=TaskQueue(self.b,0,lambda _:None)
        self.t=BattleAgent(self.b,0,SimpleNamespace(grid=self.b.grid),
            lambda kind,**kw:self.events.append(dict(kind=kind,**kw)),lambda _:None,Idle(),self.q)
        self.addCleanup(self.t.close);self.q.army_tactics=self.t
        self.base=unit(1,18,30,30,is_building=True)
        self.army=[unit(10,48,30,35,ground_dps=7),unit(11,48,120,80,ground_dps=7)]

    def w(self,sec=20,army=None,extra=()):return world([self.base]+(self.army if army is None else army)+list(extra),sec)
    def attack(self):self.q.enqueue([dict(skill='move_out',args={'scope':'all'})],20);self.q.tick(self.w(),set())
    def tags(self,actions):return {tag for a in actions if a.action_raw.unit_command.ability_id==ATTACK for tag in a.action_raw.unit_command.unit_tags}

    def test_all_bases_leave_even_without_visible_enemies(self):
        self.attack();actions=self.t.tick(self.w())
        self.assertEqual(self.tags(actions),{10,11})
        self.assertEqual(self.t.public()['all_army']['dispatched'],2)
        self.assertEqual(self.t.requests,0)

    def reply(self,mode='attack',target=(80,80)):
        groups,_=self.t._squads(self.army)
        sid=next(iter(groups))
        self.t.outbox.put(dict(request_id=1,generation=self.t.generation,wall=self.t.clock(),game_s=20.,
            response=json.dumps(dict(orders=[dict(squad=sid,mode=mode,target=list(target),reason='绕路后重新进攻')],reason='复核前线')),
            error=None,latency_s=1.,usage=None,squads={k:[u.tag for u in us] for k,us in groups.items()}))

    def test_all_army_review_is_accepted_and_route_survives_mechanical_dispatch(self):
        self.attack();self.t.tick(self.w());self.reply()
        actions=self.t.tick(self.w(sec=21))
        self.assertEqual(self.t.accepted,1);self.assertEqual(self.t.expired,0)
        self.assertIn(10,self.tags(actions))
        self.assertEqual(self.t.assignments[10]['requested_point'],(80,80))
        self.t.tick(self.w(sec=22))
        self.assertEqual(self.t.assignments[10]['requested_point'],(80,80))
        self.assertTrue(self.t.strategy['attack_authorized'])
        self.assertEqual(self.t.strategy['scope'],'all')

    def test_reinforcement_joins_reviewed_regroup_and_reports_real_target(self):
        self.attack();self.t.tick(self.w());self.reply(mode='withdraw',target=(40,40))
        self.t.tick(self.w(sec=21))
        new=unit(12,48,31,35,ground_dps=7)
        actions=self.t.tick(self.w(sec=22,extra=[new]))
        withdrawal=next(a.action_raw.unit_command for a in actions if 12 in a.action_raw.unit_command.unit_tags)
        self.assertEqual(withdrawal.ability_id,MOVE)
        self.assertEqual(self.t.assignments[12]['requested_point'],(40,40))
        arrived=[replace(u,x=40,y=40) if u.tag==10 else u for u in self.army]
        self.t.tick(self.w(sec=23,army=arrived,extra=[new]))
        row=next(r for r in self.t.public()['all_army']['units'] if r['tag']==10)
        self.assertEqual(row['state'],'at_target');self.assertEqual(row['mode'],'withdraw')
        self.assertEqual(row['requested_point'],[40,40])
        self.assertEqual(self.t.strategy['scope'],'all')

    def test_full_army_periodic_requests_continue_in_game_time_despite_dirty_frames(self):
        prompts=[];self.b.realtime=False;now=[100.]
        self.t.clock=lambda:now[0]
        self.t.client=SimpleNamespace(ready_for_request=lambda:True,
            chat=lambda system,prompt:prompts.append(json.loads(prompt)) or json.dumps(dict(orders=[],reason='继续当前进攻')))
        self.attack();self.t.tick(self.w())
        for sec in range(20,51):
            deadline=time.monotonic()+1
            while self.t.thinking and self.t.outbox.empty() and time.monotonic()<deadline:time.sleep(.001)
            self.assertFalse(self.t.thinking and self.t.outbox.empty())
            now[0]+=30  # API wall time cannot use up the game-time cooldown.
            self.t.dirty=True  # Combat observations used to bypass the cooldown.
            self.t.tick(self.w(sec=sec))
        self.assertGreaterEqual(self.t.requests,3)
        times=[p['game_s'] for p in prompts]
        self.assertTrue(all(b-a>=8 for a,b in zip(times,times[1:])))
        self.assertTrue(all(p['strategic_order']['scope']=='all' for p in prompts))
        self.assertTrue(all(p['all_army']['execution']=='mechanical_dispatch_with_periodic_llm_review' for p in prompts))

    def test_review_cannot_revoke_full_army_authorization(self):
        self.attack();self.t.tick(self.w());self.reply()
        r=self.t.outbox.get_nowait();plan=json.loads(r['response']);plan['strategic_order']={'attack_authorized':False}
        r['response']=json.dumps(plan);self.t.outbox.put(r)
        self.t.tick(self.w(sec=21))
        self.assertEqual(self.t.errors,1)
        self.assertTrue(self.t.strategy['attack_authorized']);self.assertEqual(self.t.strategy['scope'],'all')

    def test_nearest_enemy_reinforcements_wait_when_reviewed_enemy_leaves_vision(self):
        enemy=unit(99,48,90,90,alliance=4,ground_dps=7)
        self.t.mission('attack_nearest',scope='all');self.t.tick(self.w(extra=[enemy]))
        self.reply();self.t.tick(self.w(sec=21,extra=[enemy]))
        reinforcement=unit(12,48,31,35,ground_dps=7)
        self.assertNotIn(12,self.tags(self.t.tick(self.w(sec=22,extra=[reinforcement]))))
        self.assertNotIn(12,self.t.assignments)

    def test_previous_hold_orders_are_replaced(self):
        for u in self.army:self.t.assignments[u.tag]=dict(failed=False,mode='hold')
        self.attack();self.assertEqual(self.tags(self.t.tick(self.w())),{10,11})
        self.assertTrue(all(a['mode']=='attack' for a in self.t.assignments.values()))

    def test_reinforcement_in_existing_squad_gets_its_own_dispatch(self):
        self.attack();self.t.tick(self.w())
        reinforcement=unit(12,48,31,35,ground_dps=7)
        self.assertEqual(self.tags(self.t.tick(self.w(sec=21,extra=[reinforcement]))),{12})
        self.assertEqual(self.t.public()['all_army']['dispatched'],3)

    def test_scope_persists_when_later_plans_repeat_only_strategic_direction(self):
        self.attack();self.t.tick(self.w());self.t.mission('attack_nearest')
        self.assertEqual(self.t.strategy['scope'],'all')

    def test_custom_owner_is_explicitly_excluded_and_released_units_join(self):
        self.attack();self.assertEqual(self.tags(self.t.tick(self.w(),protected={10})),{11})
        self.assertEqual(self.t.public()['all_army']['counts']['excluded'],1)
        self.assertEqual(self.tags(self.t.tick(self.w(sec=21))),{10})

    def test_blocked_route_is_failure_not_departure(self):
        self.b.path_distances=lambda i,start,points:[0]*len(points)
        self.attack();self.assertFalse(self.t.tick(self.w()))
        report=self.t.public()['all_army']
        self.assertEqual(report['dispatched'],0);self.assertEqual(report['counts'],{'failed':2})

    def test_no_progress_is_visible_and_movement_is_observed(self):
        self.attack();self.t.tick(self.w())
        self.t.tick(self.w(sec=29))
        self.assertEqual(self.t.public()['all_army']['counts'],{'stalled':2})
        moved=[replace(u,x=u.x+3) for u in self.army]
        self.t.tick(self.w(sec=30,army=moved))
        self.assertEqual(self.t.public()['all_army']['counts'],{'departed':2})

    def test_scope_change_stops_previous_full_army_mission(self):
        self.attack();self.t.tick(self.w());self.t.mission('defend')
        actions=self.t.tick(self.w(sec=21))
        self.assertEqual({a.action_raw.unit_command.ability_id for a in actions},{3665})
        self.assertFalse(self.t.public()['all_army']['enabled'])

    def test_late_battle_reply_cannot_restore_old_hold_after_full_army_order(self):
        groups,_=self.t._squads(self.army)
        self.t.outbox.put(dict(request_id=1,generation=self.t.generation,wall=self.t.clock(),game_s=20.,
            response=json.dumps(dict(orders=[],reason='keep old guards')),error=None,latency_s=1.,usage=None,
            squads={k:[u.tag for u in us] for k,us in groups.items()}))
        self.attack();self.assertEqual(self.tags(self.t.tick(self.w())),{10,11})
        self.assertEqual(self.t.expired,1)

    def test_real_guard_cancellation_releases_units_for_all_army_next_tick(self):
        self.q.enqueue([dict(skill='defend_base',args=dict(base='home',units='all',count=1,reason='guard'))],20)
        self.q.tick(self.w(),set())
        self.assertIn(10,self.q.protected())
        self.q.enqueue([dict(skill='move_out',args={'scope':'all'})],21)
        self.q.tick(self.w(sec=21),set())
        self.assertFalse(self.q.defense_tags)
        self.assertEqual(self.tags(self.t.tick(self.w(sec=22),self.q.protected())),{10,11})

    def test_commander_accepts_full_army_plan_and_dispatches_across_bases(self):
        self.b.initial=[self.w()];self.b.register_action_context=lambda *a:None
        with tempfile.TemporaryDirectory() as directory:
            c=Commander(self.b,0,Idle(),opening=False,tactics_enabled=True,tactical_client=Idle(),feedback_dir=directory)
            try:
                plan=dict(tasks=[dict(skill='move_out',args={'scope':'all'})],strategic_intent='move_out',reason='采纳全军进攻')
                c.outbox.put((20,plan,None,0,True,json.dumps(plan),1,[]))
                self.assertEqual(self.tags(c.tick(self.w())),{10,11})
                self.assertEqual(c.decisions,1);self.assertEqual(c.errors,0)
                self.assertEqual(c.stats()['army_tactics']['all_army']['eligible'],2)
            finally:c.stop()

    def test_all_army_uses_every_squad_not_only_first_sixteen(self):
        units=[unit(10+i,48,10+(i%5)*30,10+(i//5)*30,ground_dps=7) for i in range(20)]
        self.t.mission('move_out',scope='all')
        self.assertEqual(self.tags(self.t.tick(self.w(army=units))),{u.tag for u in units})

    def test_nearest_enemy_without_a_target_waits_explicitly(self):
        self.t.mission('attack_nearest',scope='all');self.assertEqual(self.t.tick(self.w()),[])
        self.assertEqual(self.t.public()['all_army']['counts'],{'waiting_for_target':2})

    def test_invalid_plan_does_not_cancel_existing_guard(self):
        self.q.enqueue([dict(skill='defend_base',args=dict(base='home',units='all',reason='guard'))],20)
        guard=self.q.tasks[0]
        with self.assertRaises(PlanValidationError):
            self.q.enqueue([dict(skill='move_out',args={'scope':'all'}),dict(skill='invalid')],20)
        self.assertTrue(guard.active)
        self.q.enqueue([dict(skill='move_out',args={'scope':'all'})],20)
        self.assertFalse(guard.active)

    def test_invalid_scope_is_rejected(self):
        with self.assertRaises(PlanValidationError):self.q.enqueue([dict(skill='move_out',args={'scope':'some'})],20)

    def test_legacy_tactics_accept_available_without_silently_downgrading_all(self):
        calls=[];self.q.army_tactics=SimpleNamespace(mission=calls.append)
        self.q.enqueue([dict(skill='move_out',args={'scope':'available'})],20)
        self.q.tick(self.w(),set());self.assertEqual(calls,['move_out'])
        with self.assertRaises(PlanValidationError):self.q.enqueue([dict(skill='move_out',args={'scope':'all'})],21)

    def test_answer_schema_can_express_all_army(self):
        branches=output_schema({})['anyOf'][0]['properties']['tasks']['items']['anyOf']
        attack=next(b for b in branches if 'move_out' in b['properties']['skill']['enum'])
        self.assertIn('all',attack['properties']['args']['properties']['scope']['enum'])

    def test_provider_schema_accepts_scope_and_rejects_invalid_scope(self):
        import jsonschema
        from arena.chatgpt_planner import battle_output_schema
        schema=battle_output_schema({})
        plan=dict(tasks=[dict(skill='move_out',count=1,args={'scope':'all'})],reason='全军出动')
        jsonschema.validate(plan,schema)
        plan['tasks'][0]['args']['scope']='some'
        with self.assertRaises(jsonschema.ValidationError):jsonschema.validate(plan,schema)

    def test_coverage_failure_is_visible_in_decision_window(self):
        from llm_output_window import Feed
        feed=Feed();feed.consume(dict(record='feedback_event',event=dict(kind='all_army_coverage',
            game_loop=448,dispatched=1,total=2,departed=0,counts={'stalled':1,'excluded':1})))
        self.assertTrue(feed.entries[-1]['error'])
        self.assertIn('观察到出发 0',feed.entries[-1]['body'])


if __name__=='__main__':unittest.main()
