"""Scenario contracts using real TaskQueue/schema, synthetic own observations.

These tests prove tool execution, not that a particular LLM chose a good tactic.
The separate live probe checks actual SC2 loading/unloading and combat orders.
"""
from dataclasses import replace
import json
import tempfile
import threading
import unittest

import jsonschema
import numpy as np

from arena.battle_plans import SYSTEM, output_schema
from arena.plan_representation import translate_plan
from arena.raids import TRANSPORTS, report, HOLD
from arena.raw_engine import MOVE, ATTACK
from arena.spatial import Battlefield
from arena.tasks import TaskQueue, PlanValidationError
from test_rally_orders import engine_backend
from test_realtime_runtime import unit, world


def raid(**extra):
    args=dict(unit='zealot', count=2, targets=[[80,80],[120,120]], retreat_target=[42,55],
              retreat_enemy_count=2, dwell_s=20, reason='分兵袭矿，守军回防时撤到指定地点')
    args.update(extra)
    return dict(skill='raid', args=args)


def drop(transport='medivac', **extra):
    task=raid(unit='marine',count=2,**extra)
    task['skill']='drop_raid';task['args'].pop('targets')
    task['args'].update(transport=transport,target=[80,80])
    return task


class OffensiveMissions(unittest.TestCase):
    def setUp(self):
        self.fixture,self.b=engine_backend('protoss',available=(MOVE,ATTACK,HOLD,394,396,911,913,1406,1408,4))
        self.events=[]
        self.q=TaskQueue(self.b,0,lambda _:None,lambda k,**kw:self.events.append(dict(kind=k,**kw)))
        self.squad=[unit(i,73,30+i,30,ground_dps=10) for i in (1,2)]
        self.w=replace(world(self.squad,10),visibility=np.zeros((200,200),dtype='uint8'))

    def tick(self, units=None, seconds=None, **kw):
        self.w=replace(self.w,units=tuple(units if units is not None else self.w.units),
            game_loop=int((seconds if seconds is not None else self.w.seconds+1)*22.4),**kw)
        return self.q.tick(self.w,set())

    def start(self, task, units=None):
        if units is not None:self.w=replace(self.w,units=tuple(units))
        self.q.enqueue([task],self.w.seconds)
        return self.q.tick(self.w,set())

    def raw(self, actions, ability=None):
        return [a.action_raw.unit_command for a in actions
                if ability is None or a.action_raw.unit_command.ability_id==ability]

    def at_site(self, enemy=(), clear=False):
        units=[replace(u,x=80,y=80) for u in self.squad]+list(enemy)
        kw={'visibility':np.full((200,200),2,dtype='uint8')} if clear else {}
        return self.tick(units,**kw)

    def test_zealots_and_zerglings_split_without_taking_main_army(self):
        for kind,selector in [(73,'zealot'),(105,'zergling')]:
            with self.subTest(selector=selector):
                q=TaskQueue(self.b,0,lambda _:None)
                squad=[replace(u,type_id=kind) for u in self.squad]
                w=replace(self.w,units=tuple(squad+[unit(3,kind,20,20,ground_dps=10),unit(9,74,25,25,ground_dps=10)]))
                q.enqueue([raid(unit=selector)],w.seconds)
                actions=q.tick(w,set())
                self.assertEqual(q.tasks[0].program.tags,{1,2})
                self.assertEqual(set(self.raw(actions)[0].unit_tags),{1,2})
                self.assertEqual(len(actions),len(q.action_contexts))

    def test_visible_expansion_is_attacked_without_claiming_a_kill(self):
        self.start(raid())
        base=unit(90,18,83,80,alliance=4,is_building=True)
        actions=self.at_site([base]);cmd=self.raw(actions,ATTACK)[0]
        self.assertEqual(cmd.target_unit_tag,90)
        self.assertTrue(self.q.tasks[0].active)
        self.assertNotIn('victory',self.q.tasks[0].reason)

    def test_workers_are_prioritized_and_do_not_trigger_retreat(self):
        self.start(raid(retreat_enemy_count=1))
        base=unit(90,18,80,80,alliance=4,is_building=True)
        worker=unit(91,45,83,80,alliance=4,ground_dps=5)
        cmd=self.raw(self.at_site([base,worker]),ATTACK)[0]
        self.assertEqual(cmd.target_unit_tag,91)
        self.assertEqual(self.q.tasks[0].program.phase,'raiding')

    def test_current_defenders_trigger_move_to_exact_model_retreat_point(self):
        self.start(raid());self.at_site([unit(90,45,82,80,alliance=4)])
        defenders=[unit(i,48,84+i-90,80,alliance=4,ground_dps=8) for i in (91,92)]
        actions=self.at_site(defenders)
        cmd=self.raw(actions,MOVE)[0]
        self.assertAlmostEqual(cmd.target_world_space_pos.x,41.5)
        self.assertAlmostEqual(cmd.target_world_space_pos.y,54.5)
        self.assertEqual(self.q.tasks[0].program.phase,'withdraw')
        self.assertEqual(len(self.q.tasks[0].program.defender_contacts),2)
        self.assertFalse(self.raw(actions,ATTACK))
        self.assertEqual(self.q.tasks[0].program.args['retreat_target'],[42.,55.])

    def test_one_defender_below_selected_threshold_does_not_retreat(self):
        self.start(raid())
        self.at_site([unit(91,48,83,80,alliance=4,ground_dps=8)])
        self.assertEqual(self.q.tasks[0].program.phase,'raiding')

    def test_armed_structure_counts_as_a_defender(self):
        self.start(raid(retreat_enemy_count=1))
        self.at_site([unit(91,66,83,80,alliance=4,ground_dps=8,is_building=True)])
        self.assertEqual(self.q.tasks[0].program.phase,'withdraw')

    def test_defender_outside_model_radius_does_not_trigger(self):
        self.start(raid(retreat_enemy_count=1,threat_radius=4))
        self.at_site([unit(91,48,90,80,alliance=4,ground_dps=8)])
        self.assertEqual(self.q.tasks[0].program.phase,'raiding')

    def test_fog_is_unknown_and_never_attacked_as_remembered_base(self):
        self.start(raid());actions=self.at_site()
        self.assertEqual(actions,[])
        self.assertEqual(self.q.tasks[0].program.step,0)
        self.assertIn('unknown',self.q.tasks[0].reason)

    def test_visible_empty_site_advances_only_model_route(self):
        self.start(raid());actions=self.at_site(clear=True)
        self.assertEqual(self.q.tasks[0].program.step,1)
        self.assertEqual(self.q.tasks[0].program.requested_route,[(80.,80.),(120.,120.)])
        self.assertEqual(len(self.raw(actions,MOVE)),1)
        self.assertEqual(self.q.tasks[0].program.arrivals[0]['visible_economy'],0)

    def test_dwell_expiry_with_enemy_still_alive_withdraws_without_clearance_claim(self):
        self.start(raid(targets=[[80,80]],dwell_s=1))
        base=unit(90,18,83,80,alliance=4,is_building=True)
        self.at_site([base]);self.at_site([base]);self.at_site([base])
        self.assertEqual(self.q.tasks[0].program.phase,'withdraw')
        self.assertEqual(self.q.tasks[0].program.arrivals[0]['visible_economy'],1)

    def test_retreat_holds_survivors_and_does_not_rejoin_main_army(self):
        self.start(raid(targets=[[80,80]],dwell_s=1));self.at_site();self.at_site()
        home=[replace(u,x=42,y=55) for u in self.squad]
        actions=self.tick(home)
        self.assertEqual(self.q.tasks[0].program.phase,'holding')
        self.assertEqual(len(self.raw(actions,HOLD)),1)
        self.assertEqual(self.q.protected(),{1,2})
        self.assertEqual(self.tick(home),[])
        self.q.enqueue([dict(skill='cancel_unit_order',args={'task_id':1}),
                        dict(skill='order_units',args=dict(unit='zealot',count=2,target=[90,90],mode='move',reason='重新分配'))],self.w.seconds)
        actions=self.tick(home)
        self.assertEqual([a.ability_id for a in self.raw(actions)],[4,MOVE])
        self.assertEqual(self.q.tasks[1].program.tags,{1,2})

    def test_dead_members_are_not_replaced_by_new_troops(self):
        self.start(raid());new=unit(3,73,31,30,ground_dps=10)
        self.tick([self.squad[0],new],dead_units=(2,))
        self.assertEqual(self.q.tasks[0].program.tags,{1})

    def test_unobserved_member_without_death_or_cargo_proof_waits(self):
        self.start(raid())
        self.assertEqual(self.tick([self.squad[0]]),[])
        self.assertEqual(self.q.tasks[0].program.tags,{1,2})
        self.assertIn('unobserved',self.q.tasks[0].reason)

    def test_defense_and_other_owners_are_not_taken(self):
        self.q.defense_tags={1};self.start(raid())
        self.assertEqual(self.q.tasks[0].program.tags,set())
        self.assertEqual(self.q.tasks[0].status,'waiting')

    def test_bad_plan_is_rejected_atomically(self):
        for extra in [dict(retreat_target=[-1,0]),dict(retreat_enemy_count=True),dict(dwell_s=float('nan')),
                      dict(count=33),dict(targets='expansion_sites'),dict(retreat_target='invented')]:
            with self.subTest(extra=extra),self.assertRaises(PlanValidationError):
                self.q.enqueue([dict(skill='train_worker'),raid(**extra)],10)
            self.assertEqual(self.q.tasks,[])

    def test_deadline_is_original_and_holding_does_not_reset_it(self):
        self.start(raid(timeout_s=2));self.tick(seconds=13)
        self.assertEqual(self.q.tasks[0].status,'failed');self.assertEqual(self.q.protected(),set())
        self.assertTrue(any(e['kind']=='spatial_mission_report' for e in self.events))

    def transport_fixture(self, name='medivac'):
        kind=TRANSPORTS[name][0]
        self.squad=[unit(i,48,30+i,30,ground_dps=10) for i in (1,2)]
        return unit(10,kind,30,30,flying=True,cargo_max=8)

    def load_all(self, tr, name='medivac', **args):
        actions=self.start(drop(name,**args),self.squad+[tr])
        self.assertEqual(self.raw(actions)[0].ability_id,TRANSPORTS[name][1])
        self.assertEqual(self.raw(actions)[0].target_unit_tag,1)
        self.assertEqual(self.tick(self.squad+[tr]),[])
        tr=replace(tr,passengers=(1,),cargo_taken=1)
        actions=self.tick([self.squad[1],tr])
        self.assertEqual(self.raw(actions)[0].target_unit_tag,2)
        tr=replace(tr,passengers=(1,2),cargo_taken=2)
        actions=self.tick([tr])
        self.assertEqual(self.q.tasks[0].program.phase,'fly')
        self.assertEqual(self.raw(actions)[0].ability_id,MOVE)
        self.assertEqual(self.q.protected(),{1,2,10})
        return tr

    def test_all_three_transports_load_fly_unload_attack_and_return(self):
        for name in TRANSPORTS:
            with self.subTest(transport=name):
                self.setUp();tr=self.transport_fixture(name);tr=self.load_all(tr,name)
                tr=replace(tr,x=80,y=80)
                actions=self.tick([tr]);self.assertEqual(self.raw(actions)[0].ability_id,TRANSPORTS[name][2])
                self.assertEqual(self.q.tasks[0].program.phase,'unload')
                self.assertEqual(self.tick([tr]),[])
                tr=replace(tr,passengers=(),cargo_taken=0)
                worker=unit(90,45,82,80,alliance=4,ground_dps=5)
                squad=[replace(u,x=80,y=80) for u in self.squad]
                actions=self.tick(squad+[tr,worker])
                self.assertEqual(self.q.tasks[0].program.phase,'raiding')
                self.assertEqual(self.raw(actions,ATTACK)[0].target_unit_tag,90)
                self.assertEqual(list(self.raw(actions,MOVE)[0].unit_tags),[10])

    def test_defenders_before_unload_withdraw_with_cargo_no_drop(self):
        tr=self.load_all(self.transport_fixture(),retreat_enemy_count=1)
        tr=replace(tr,x=80,y=80)
        defender=unit(90,48,82,80,alliance=4,ground_dps=5)
        actions=self.tick([tr,defender])
        self.assertFalse(self.raw(actions,396));self.assertEqual(self.q.tasks[0].program.phase,'withdraw')
        self.tick([replace(tr,x=42,y=55)])
        self.assertEqual(self.q.tasks[0].program.phase,'holding')
        self.assertEqual(self.q.tasks[0].program.cargo['passengers'],[1,2])

    def test_capacity_overflow_fails_without_load_or_extra_transport(self):
        tr=replace(self.transport_fixture(),cargo_max=1)
        actions=self.start(drop(),self.squad+[tr])
        self.assertEqual(actions,[])
        self.assertEqual(self.q.tasks[0].status,'failed')
        self.assertIn('capacity',self.q.tasks[0].reason)

    def test_busy_transport_is_never_taken(self):
        tr=replace(self.transport_fixture(),passengers=(99,),cargo_taken=1)
        self.assertEqual(self.start(drop(),self.squad+[tr]),[])
        self.assertEqual(self.q.tasks[0].program.tags,set())

    def test_loading_is_observed_not_inferred_from_missing_ground_unit(self):
        tr=self.transport_fixture();self.start(drop(),self.squad+[tr])
        self.tick([self.squad[1],tr])
        self.assertEqual(self.q.tasks[0].program.phase,'load')
        self.assertIn('unobserved',self.q.tasks[0].reason)

    def test_load_failure_does_not_recast_or_allocate_replacements(self):
        tr=self.transport_fixture();self.start(drop(),self.squad+[tr])
        actions=self.tick(self.squad+[tr],seconds=31)
        self.assertEqual(self.q.tasks[0].status,'failed')
        self.assertFalse(self.raw(actions,394))

    def test_unload_requires_all_survivors_on_ground(self):
        tr=self.load_all(self.transport_fixture());tr=replace(tr,x=80,y=80)
        self.tick([tr]);self.tick([replace(tr,passengers=(2,),cargo_taken=1),replace(self.squad[0],x=80,y=80)])
        self.assertEqual(self.q.tasks[0].program.phase,'unload')
        self.assertFalse(self.raw(self.tick([tr],seconds=36),ATTACK))
        self.assertEqual(self.q.tasks[0].status,'failed')

    def test_model_air_waypoints_are_preserved_in_order(self):
        tr=self.load_all(self.transport_fixture(),waypoints=[[35,70],[60,90]])
        mission=self.q.tasks[0].program
        self.assertEqual(mission.air_route,[(35.,70.),(60.,90.),(80.,80.)])
        self.tick([replace(tr,x=35,y=70)])
        actions=self.tick([replace(tr,x=36,y=70)])
        self.assertEqual(mission.air_step,1)
        self.assertEqual(self.raw(actions,MOVE)[0].target_world_space_pos.y,90)

    def test_native_rejection_is_visible_and_not_retried(self):
        tr=self.transport_fixture();self.start(drop(),self.squad+[tr])
        actions=self.tick(self.squad+[tr],errors=((10,394,44),))
        self.assertEqual(self.q.tasks[0].status,'failed')
        self.assertIn('engine rejected',self.q.tasks[0].reason)
        self.assertFalse(self.raw(actions,394))

    def test_lost_worker_visibility_cancels_old_targeted_chase(self):
        self.start(raid())
        self.at_site([unit(90,45,82,80,alliance=4)])
        actions=self.at_site()
        self.assertEqual(len(self.raw(actions,MOVE)),1)
        self.assertFalse(self.raw(actions,ATTACK))
        self.assertEqual(self.q.tasks[0].program.step,0)

    def test_cancel_drop_stops_only_observed_actors_without_replacing_cargo(self):
        tr=self.load_all(self.transport_fixture())
        self.q.enqueue([dict(skill='cancel_task',args={'task_id':1})],self.w.seconds)
        actions=self.tick([tr])
        self.assertEqual(self.q.tasks[0].status,'cancelled')
        self.assertEqual(list(self.raw(actions,4)[0].unit_tags),[10])
        self.assertEqual(self.q.protected(),set())
        self.assertFalse(self.raw(actions,396))

    def test_confirmed_transport_death_does_not_infer_passenger_survival_or_buy_replacement(self):
        tr=self.load_all(self.transport_fixture())
        actions=self.tick([],dead_units=(10,))
        self.assertEqual(self.q.tasks[0].status,'failed')
        self.assertIn('passenger fate not inferred',self.q.tasks[0].reason)
        self.assertEqual(actions,[])

    def test_unavailable_load_waits_with_original_squad_and_does_not_fly(self):
        self.b.available_abilities=lambda i,tags,**kw:{t:{MOVE,ATTACK} for t in tags}
        tr=self.transport_fixture()
        self.assertEqual(self.start(drop(),self.squad+[tr]),[])
        self.assertEqual(self.q.tasks[0].program.phase,'load')
        self.assertEqual(self.q.protected(),{1,2,10})
        self.assertIsNone(self.q.tasks[0].program.pending_load)

    def test_duplicate_intent_keeps_original_task_deadline_and_squad(self):
        self.start(raid());task=self.q.tasks[0]
        self.q.enqueue([raid(reason='相同任务的新表述')],30)
        self.assertEqual(len(self.q.tasks),1)
        self.assertEqual(task.created,10)
        self.assertEqual(task.program.tags,{1,2})

    def test_ground_approach_queries_are_cached_instead_of_sent_every_frame(self):
        calls=[];query=self.b.path_distances
        self.b.path_distances=lambda *args:(calls.append(args) or query(*args))
        self.start(raid());initial=len(calls)
        self.assertGreater(initial,0)
        for _ in range(10):self.tick()
        self.assertEqual(len(calls),initial)
        self.assertIsNotNone(self.q.tasks[0].program.resolved_ground_destination)

    def test_enemy_economy_outside_authored_site_is_not_targeted(self):
        self.start(raid())
        self.assertFalse(self.raw(self.at_site([unit(90,45,100,80,alliance=4)]),ATTACK))

    def test_visible_flying_base_is_reported_unattackable_not_an_empty_site(self):
        self.start(raid())
        base=unit(90,18,80,80,alliance=4,is_building=True,flying=True)
        actions=self.at_site([base],clear=True)
        mission=self.q.tasks[0].program
        self.assertFalse(self.raw(actions,ATTACK));self.assertEqual(mission.step,0)
        self.assertEqual(mission.contacts[0]['tag'],90)
        self.assertFalse(mission.contacts[0]['attackable'])
        self.assertIn('not attackable',self.q.tasks[0].reason)

    def test_model_schema_and_prompt_expose_raid_contract(self):
        schema=output_schema({'control_policy':{'spatial_missions_available':True}})
        for task in (raid(),drop()):jsonschema.validate(dict(tasks=[task],reason='验证战术'),schema)
        self.assertIn('retreat_enemy_count',SYSTEM);self.assertIn('drop_raid',SYSTEM)
        bad=drop();bad['args'].pop('retreat_target')
        with self.assertRaises(jsonschema.ValidationError):jsonschema.validate(dict(tasks=[bad],reason='缺少撤退点'),schema)

    def test_representation_retains_model_squad_and_retreat_choice(self):
        task=raid();task['count']=task['args'].pop('count');task['args']['unit']='Zealot'
        result,changes=translate_plan(dict(tasks=[task]),{'battlefield':{'unit_selectors':['zealot']}})
        self.assertEqual(result['tasks'][0]['args']['count'],2)
        self.assertEqual(result['tasks'][0]['args']['retreat_target'],[42,55])
        self.assertEqual(result['tasks'][0]['args']['unit'],'zealot')

    def test_observation_exposes_economic_occupancy_and_own_transport_capacity(self):
        tr=self.transport_fixture();base=unit(90,18,170,170,alliance=4,is_building=True)
        worker=unit(91,45,171,170,alliance=4,ground_dps=5)
        w=replace(self.w,units=tuple(self.squad+[tr,base,worker]))
        area=next(a for a in Battlefield(self.b,0).public(w)['areas'] if a['id']=='enemy_start')
        self.assertEqual(area['visible_enemy_townhalls'],1);self.assertEqual(area['visible_enemy_workers'],1)
        self.assertEqual(area['visible_enemy_combat_defenders'],0)
        inventory=report(w,self.q)
        self.assertEqual(inventory['transports'][0]['cargo_max'],8)
        self.assertFalse(inventory['transports'][0]['allocated'])

    def test_actual_commander_request_contains_raid_state_and_transport_inventory(self):
        from arena.commander import Commander
        class Capture:
            compact_battle=True
            soft_timeout=5;timeout=10
            def __init__(self):self.done=threading.Event()
            def chat(self, system, prompt):
                self.system=system;self.payload=json.loads(prompt);self.done.set()
                return json.dumps(dict(tasks=[],reason='读取小队状态，等待后续指挥'))
        tr=unit(10,81,30,30,flying=True,cargo_max=8)
        w=replace(self.w,units=tuple(self.squad+[tr]))
        self.b.initial=[w];self.b.register_action_context=lambda *a:None
        client=Capture()
        with tempfile.TemporaryDirectory() as root:
            c=Commander(self.b,0,client,opening=False,feedback_dir=root)
            try:
                c.tasks.enqueue([raid()],w.seconds)
                c.tick(w)
                self.assertTrue(client.done.wait(2))
                self.assertEqual(client.payload['active_tasks'][0]['workflow']['phase'],'raiding')
                self.assertEqual(client.payload['offensive_operations']['free_ground_combat'],{})
                self.assertEqual(client.payload['offensive_operations']['transports'][0]['selector'],'warp_prism')
                self.assertIn('retreat_target',client.system)
                self.assertIn('drop_raid',client.system)
            finally:c.stop()

    def test_legacy_backend_hides_unsupported_raid_tools(self):
        from arena.commander import Commander
        from test_realtime_runtime import Backend
        from verify_rally_orders_live import Idle
        b=Backend();b.initial=[self.w]
        with tempfile.TemporaryDirectory() as root:
            c=Commander(b,0,Idle(),opening=False,feedback_dir=root)
            try:
                self.assertFalse(c.control_policy['spatial_missions_available'])
                self.assertNotIn('drop_raid',c.system)
            finally:c.stop()


if __name__=='__main__':unittest.main()
