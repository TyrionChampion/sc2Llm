"""Player-local fixtures; engine movement/visibility is checked separately by native probe."""
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import numpy as np

from arena.raw_engine import MOVE, ATTACK
from arena.spatial import Battlefield, area_visibility, validate_args
from arena.tasks import TaskQueue, PlanValidationError
from arena.combat_micro import CombatMicro
from arena.battle_plans import output_schema, SYSTEM
from test_rally_orders import engine_backend
from test_realtime_runtime import unit, world


def order(target=(80,80), **kw):
    return dict(skill='order_units',args=dict(unit='marine',count=1,mode='attack_move',target=list(target),reason='模型指定目标',**kw))

def scout(targets='expansion_sites', **kw):
    return dict(skill='scout',args=dict(unit='marine',count=1,targets=targets,reason='模型主动侦察',**kw))

class SpatialMissions(unittest.TestCase):
    def setUp(self):
        self.fixture,self.backend=engine_backend('terran',available=(MOVE,ATTACK,3673,390))
        self.events=[]
        self.q=TaskQueue(self.backend,0,lambda s:None,lambda kind,**kw:self.events.append(dict(kind=kind,**kw)))
        self.marine=unit(1,48,30,30)
        self.w=replace(world([self.marine],10),visibility=np.zeros((200,200),dtype='uint8'))

    def start(self, task):
        self.q.enqueue([task],self.w.seconds)
        return self.q.tick(self.w,set())
    def test_newborn_squad_waits_for_native_control_without_a_stop_or_replacement(self):
        self.backend.available_abilities=lambda i,tags,**kw:{tag:{1,4} for tag in tags}
        self.assertEqual(self.start(order(timeout_s=90)),[])
        task=self.q.tasks[0]
        self.assertTrue(task.active);self.assertEqual(task.program.tags,{1})
        self.assertEqual(task.program.summary()['native_ability_wait']['missing_tags'],[1])
        self.backend.available_abilities=lambda i,tags,**kw:{tag:{MOVE,ATTACK} for tag in tags}
        out=self.q.tick(replace(self.w,game_loop=self.w.game_loop+23),set())
        self.assertEqual(len(out),1);self.assertEqual(out[0].action_raw.unit_command.ability_id,ATTACK)
        self.assertEqual(list(out[0].action_raw.unit_command.unit_tags),[1])
        self.assertIsNone(task.program.summary().get('native_ability_wait'))
    def test_unavailable_squad_ability_has_a_bounded_grace_with_the_original_deadline(self):
        self.backend.available_abilities=lambda i,tags,**kw:{tag:set() for tag in tags}
        self.assertEqual(self.start(order(timeout_s=90)),[])
        task=self.q.tasks[0]
        self.assertEqual(self.q.tick(replace(self.w,game_loop=self.w.game_loop+int(7*22.4)),set()),[])
        self.assertTrue(task.active)
        self.q.tick(replace(self.w,game_loop=self.w.game_loop+int(9*22.4)),set())
        self.assertEqual(task.status,'failed');self.assertIn('ability unavailable',task.reason)
        self.assertEqual(task.program.summary()['native_ability_wait']['missing_tags'],[1])
        short=TaskQueue(self.backend,0,lambda _:None)
        short.enqueue([order(timeout_s=1)],self.w.seconds)
        self.assertEqual(short.tick(self.w,set()),[])
        self.assertEqual(short.tasks[0].program.summary()['native_ability_wait']['deadline_game_s'],self.w.seconds+1)
        short.tick(replace(self.w,game_loop=self.w.game_loop+45),set())
        self.assertEqual(short.tasks[0].status,'failed')
        self.assertIn('mission game-time deadline',short.tasks[0].reason)

    def test_native_unit_paths_report_blockage_and_own_structures_without_choosing_a_recovery(self):
        calls=[];distance=[0]
        def query(index,tags,target):
            calls.append((index,tags,target));return [distance[0] for _ in tags]
        self.backend.unit_path_distances=query
        depot=unit(2,19,31,32,is_building=True)
        enemy=replace(depot,tag=3,alliance=4)
        far=replace(depot,tag=4,x=150,y=150)
        self.w=replace(self.w,units=(self.marine,depot,enemy,far))
        actions=self.start(order(timeout_s=120));self.assertEqual(len(actions),1)
        self.assertEqual(actions[0].action_raw.unit_command.ability_id,ATTACK)
        report=self.q.tasks[0].program.summary()['native_paths']
        self.assertEqual(report['blocked_tags'],[1])
        self.assertEqual([u['tag'] for u in report['own_nearby_structures']],[2])
        self.assertEqual(report['target'],list(self.q.tasks[0].program.route[0]))
        self.assertEqual(len(calls),1)
        self.q.tick(replace(self.w,game_loop=int(11*22.4)),set());self.assertEqual(len(calls),1)
        distance[0]=100
        self.assertEqual(self.q.tick(replace(self.w,game_loop=int(14*22.4)),set()),[])
        self.assertEqual(self.q.tasks[0].program.summary()['native_paths']['blocked_tags'],[])
        self.assertEqual(self.q.tasks[0].program.args['target'],[80.0,80.0])

    def test_path_query_failure_is_unavailable_rather_than_a_false_blocked_route(self):
        self.backend.unit_path_distances=lambda *args:[]
        self.assertEqual(len(self.start(order(timeout_s=120))),1)
        report=self.q.tasks[0].program.summary()['native_paths']
        self.assertEqual(report['status'],'unavailable');self.assertNotIn('blocked_tags',report)

    def test_empty_queue_never_starts_search(self):
        for s in (10,50,200):
            self.assertEqual(self.q.tick(replace(self.w,game_loop=int(s*22.4)),set()),[])
        self.assertEqual(self.q.tasks,[])
        self.assertFalse(any(e['kind'].startswith('spatial') for e in self.events))

    def test_explicit_move_targets_map_point_not_enemy_start(self):
        actions=self.start(order())
        c=actions[0].action_raw.unit_command
        self.assertEqual(c.ability_id,ATTACK)
        self.assertEqual(list(c.unit_tags),[1])
        self.assertAlmostEqual(c.target_world_space_pos.x,79.5)
        self.assertEqual(len(actions),len(self.q.action_contexts))
        self.assertIn(1,self.q.protected())
        self.assertTrue(any(e['kind']=='spatial_target_resolved' for e in self.events))

    def test_explicit_route_preserves_model_order_and_reports_arrival(self):
        self.start(scout([[50,50],[80,80]]))
        w=replace(self.w,game_loop=246,units=(replace(self.marine,x=50.5,y=50.5),))
        actions=self.q.tick(w,set())
        self.assertEqual(len(actions),1)
        self.assertAlmostEqual(actions[0].action_raw.unit_command.target_world_space_pos.x,79.5)
        t=self.q.tasks[0]
        self.assertEqual(t.program.step,1)
        w=replace(w,game_loop=270,units=(replace(self.marine,x=80.5,y=80.5),))
        self.assertEqual(self.q.tick(w,set()),[])
        self.assertEqual(t.status,'completed')
        self.assertEqual(len(t.program.arrivals),2)
        self.assertIn('not victory',t.reason)
        self.assertEqual(self.q.protected(),set())

    def test_contact_stops_and_never_starts_attack_or_next_search(self):
        self.start(scout([[80,80]]))
        enemy=unit(2,48,35,35,alliance=4)
        w=replace(self.w,game_loop=246,units=(self.marine,enemy))
        actions=self.q.tick(w,set())
        self.assertEqual([a.action_raw.unit_command.ability_id for a in actions],[4])
        self.assertEqual(self.q.tasks[0].status,'completed')
        contacts=[e for e in self.events if e['kind']=='scout_contact']
        self.assertEqual(len(contacts),1)
        self.assertEqual(contacts[0]['contacts'][0]['pos'],[35,35])
        self.assertTrue(contacts[0]['mission_stopped_on_contact'])
        self.assertFalse(contacts[0]['goal_arrived'])
        self.assertEqual(contacts[0]['requested_goal'],[80,80])
        self.assertEqual(contacts[0]['confirmed_arrivals'],[])
        self.assertEqual(contacts[0]['remaining_requested_route'],[[80,80]])
        self.assertEqual(self.q.tick(replace(w,game_loop=400),set()),[])
        self.assertEqual(len(self.q.tasks),1)

    def test_contact_at_the_goal_reports_actual_arrival_without_inventing_route_completion(self):
        self.start(scout([[80,80],[100,100]]))
        pos=self.q.tasks[0].program.route[0]
        w=replace(self.w,game_loop=246,units=(replace(self.marine,x=pos[0],y=pos[1]),
            unit(2,48,pos[0]+1,pos[1]+1,alliance=4)))
        self.q.tick(w,set())
        contact=next(e for e in self.events if e['kind']=='scout_contact')
        self.assertTrue(contact['goal_arrived']);self.assertEqual(contact['resolved_goal'],list(pos))
        self.assertEqual(contact['confirmed_arrivals'],[])
        self.assertEqual(contact['remaining_requested_route'],[[80,80],[100,100]])

    def test_explicit_scout_continues_route_past_worker_without_contact_spam_or_attack(self):
        self.start(scout([[80,80],[100,100]],stop_on_contact=False))
        enemy=unit(2,45,35,35,alliance=4)
        w=replace(self.w,game_loop=246,units=(self.marine,enemy))
        self.assertEqual(self.q.tick(w,set()),[])
        task=self.q.tasks[0]
        self.assertEqual(task.status,'running');self.assertEqual(self.q.protected(),{1})
        contact=next(e for e in self.events if e['kind']=='scout_contact')
        self.assertFalse(contact['mission_stopped_on_contact']);self.assertFalse(contact['goal_arrived'])
        self.assertEqual(contact['confirmed_arrivals'],[])
        self.assertEqual(contact['remaining_requested_route'],[[80,80],[100,100]])
        self.assertEqual(self.q.tick(replace(w,game_loop=260),set()),[])
        self.assertEqual(len([e for e in self.events if e['kind']=='scout_contact']),1)
        # Contact at the first waypoint does not abandon the explicit second waypoint.
        w=replace(w,game_loop=270,units=(replace(self.marine,x=80,y=80),replace(enemy,x=83,y=83)))
        actions=self.q.tick(w,set())
        self.assertEqual([a.action_raw.unit_command.ability_id for a in actions],[MOVE])
        self.assertEqual(task.program.step,1);self.assertEqual(len(task.program.arrivals),1)
        w=replace(w,game_loop=290,units=(replace(self.marine,x=100,y=100),replace(enemy,x=103,y=103)))
        self.assertEqual(self.q.tick(w,set()),[])
        self.assertEqual(task.status,'completed');self.assertEqual(len(task.program.arrivals),2)
        self.assertEqual(self.q.protected(),set())

    def test_continuing_scout_reports_new_or_returning_contacts(self):
        self.start(scout([[80,80]],stop_on_contact=False))
        enemy=unit(2,45,35,35,alliance=4)
        w=replace(self.w,game_loop=246,units=(self.marine,enemy))
        self.q.tick(w,set())
        self.q.tick(replace(w,game_loop=260,units=(self.marine,)),set())
        self.q.tick(replace(w,game_loop=270),set())
        self.q.tick(replace(w,game_loop=280,units=w.units+(unit(3,45,35,35,alliance=4),)),set())
        self.assertEqual(len([e for e in self.events if e['kind']=='scout_contact']),3)
        self.assertEqual(self.q.tasks[0].status,'running')

    def test_continuing_scout_can_dispatch_with_worker_already_visible(self):
        self.w=replace(self.w,units=(self.marine,unit(2,45,35,35,alliance=4)))
        actions=self.start(scout([[80,80]],stop_on_contact=False))
        self.assertEqual([a.action_raw.unit_command.ability_id for a in actions],[MOVE])
        self.assertEqual(list(actions[0].action_raw.unit_command.unit_tags),[1])
        self.assertEqual(self.q.tasks[0].status,'running')
        self.assertFalse(next(e for e in self.events if e['kind']=='scout_contact')['mission_stopped_on_contact'])

    def test_continuing_scout_retains_original_deadline_and_has_no_replacement(self):
        self.start(scout([[80,80]],stop_on_contact=False,timeout_s=5))
        enemy=unit(2,45,35,35,alliance=4)
        self.q.tick(replace(self.w,game_loop=246,units=(self.marine,enemy)),set())
        self.q.tick(replace(self.w,game_loop=400,units=(self.marine,enemy)),set())
        self.assertEqual(self.q.tasks[0].status,'failed');self.assertEqual(len(self.q.tasks),1)
        self.assertEqual(self.q.protected(),set())

    def test_stop_on_contact_is_scout_only_and_strictly_boolean(self):
        for value in (0,1,'false',None,[]):
            with self.subTest(value=value),self.assertRaises(ValueError):
                validate_args('scout',scout([[80,80]],stop_on_contact=value)['args'],(200,200))
        with self.assertRaises(ValueError):
            validate_args('order_units',order(stop_on_contact=False)['args'],(200,200))

    def test_cancel_then_explicit_redeploy_same_plan(self):
        self.start(scout([[80,80]]))
        self.q.enqueue([dict(skill='cancel_unit_order',args={'task_id':1}),order((100,100))],10)
        actions=self.q.tick(self.w,set())
        self.assertEqual([a.action_raw.unit_command.ability_id for a in actions],[4,ATTACK])
        self.assertEqual(self.q.tasks[0].status,'cancelled')
        self.assertEqual(len(actions),len(self.q.action_contexts))
        self.assertEqual(self.q.tasks[1].program.tags,{1})

    def test_idle_attack_move_formation_near_goal_is_arrival_without_stop_or_victory(self):
        # Real match3 error2184: all native orders/engagements ended, several
        # outer members >4 tiles away due to formation spacing.
        positions=[(146.66,55.17),(145.75,57.31),(144.06,54.07),(146.52,58.96),(151.52,55.48)]
        troops=[unit(n+1,48,30+n,30,radius=.625,ground_dps=10,attack_range=6) for n in range(len(positions))]
        self.w=replace(self.w,units=tuple(troops));self.start(
            dict(skill='order_units',args=dict(unit='army',count='all',mode='attack_move',target=[150,54],reason='显式进攻')))
        arrived=tuple(replace(u,x=x,y=y) for u,(x,y) in zip(troops,positions))
        w=replace(self.w,game_loop=246,units=arrived)
        self.assertEqual(self.q.tick(w,set()),[])
        self.assertEqual(self.q.tasks[0].status,'completed')
        self.assertIn('not victory',self.q.tasks[0].reason)
        self.assertTrue(any(e['kind']=='spatial_arrival' and 'settled_formation' in e['observation'] for e in self.events))

    def test_visible_in_range_enemy_prevents_settled_attack_arrival(self):
        troops=[unit(1,48,30,30,radius=.375,ground_dps=10,attack_range=5),
                unit(2,48,31,30,radius=.375,ground_dps=10,attack_range=5)]
        self.w=replace(self.w,units=tuple(troops));self.start(dict(skill='order_units',args=dict(
            unit='army',count='all',mode='attack_move',target=[80,80],reason='显式进攻')))
        arrived=[replace(troops[0],x=79.5,y=79.5),replace(troops[1],x=85,y=79.5)]
        enemy=unit(3,18,80,80,alliance=4,is_building=True,radius=2.5)
        self.q.tick(replace(self.w,game_loop=246,units=tuple(arrived+[enemy])),set())
        self.assertNotEqual(self.q.tasks[0].status,'completed')

    def test_explicit_order_can_target_threatened_entrance_while_automatic_rally_stays_safe(self):
        from arena.rally import named_rally_point
        from arena.spatial import resolve_target
        home=unit(10,18,30,30,is_building=True,radius=2.5)
        base=unit(11,18,70,70,is_building=True,radius=2.5)
        w=replace(self.w,units=(self.marine,home,base))
        grid=self.q._rally_grid();point,_=named_rally_point(grid,self.backend,0,'expansion_entrance',w)
        enemy=unit(12,48,*point,alliance=4,ground_dps=10,attack_range=5)
        contested=replace(w,units=w.units+(enemy,))
        with self.assertRaisesRegex(ValueError,'blocked or threatened'):
            named_rally_point(grid,self.backend,0,'expansion_entrance',contested)
        self.assertEqual(resolve_target('expansion_entrance',contested,self.q),point)
        self.q.enqueue([dict(skill='order_units',args=dict(unit='marine',count=1,mode='attack_move',
            target='expansion_entrance',reason='模型显式增援受压矿口'))],contested.seconds)
        actions=self.q.tick(contested,set())
        self.assertTrue(any(a.action_raw.unit_command.ability_id==ATTACK for a in actions))

    def test_duplicate_reason_change_does_not_allocate_twice(self):
        self.start(order())
        other=order();other['args']['reason']='另一种表述'
        self.q.enqueue([other],10)
        self.assertEqual(len(self.q.tasks),1)

    def test_allocated_squads_do_not_overlap(self):
        self.start(order())
        self.q.enqueue([scout([[100,100]])],10)
        self.assertEqual(self.q.tick(self.w,set()),[])
        self.assertEqual(self.q.tasks[1].status,'waiting')
        self.assertFalse(self.q.tasks[1].program.tags)

    def test_missing_destination_is_failed_visibly_without_home_substitute(self):
        self.start(order());self.q.enqueue([scout(['site_999999'])],10)
        actions=self.q.tick(self.w,set())
        self.assertEqual(actions,[])
        self.assertEqual(self.q.tasks[1].status,'failed')
        self.assertIn('site_999999',self.q.tasks[1].reason)
        self.assertTrue(any(e['kind']=='spatial_mission_error' and e['traceback'] for e in self.events))

    def test_ground_unreachable_is_not_blindly_dispatched(self):
        self.backend.path_distances=lambda i,start,points:[0]*len(points)
        actions=self.start(order())
        self.assertNotIn(ATTACK,[a.action_raw.unit_command.ability_id for a in actions])
        self.assertEqual(self.q.tasks[0].status,'failed')
        self.assertIn('unreachable',self.q.tasks[0].reason)

    def test_occupied_goal_uses_reported_local_approach_not_another_region(self):
        self.backend.path_distances=lambda i,start,points:[0 if abs(p[0]-80)<1 and abs(p[1]-80)<1 else 1 for p in points]
        actions=self.start(order())
        p=actions[0].action_raw.unit_command.target_world_space_pos
        import math
        self.assertLessEqual(math.dist((p.x,p.y),(80,80)),6.75)
        resolved=[e for e in self.events if e['kind']=='spatial_target_resolved']
        self.assertEqual(resolved[0]['requested'],[80,80])
        self.assertEqual(resolved[0]['actual'],[p.x,p.y])

    def test_engine_rejection_never_marks_arrival_or_retries(self):
        self.start(order())
        actions=self.q.tick(replace(self.w,game_loop=246,errors=((1,ATTACK,3),)),set())
        self.assertEqual([a.action_raw.unit_command.ability_id for a in actions],[4])
        self.assertEqual(self.q.tasks[0].status,'failed')
        self.assertEqual(self.q.tasks[0].program.arrivals,[])

    def test_hidden_passenger_preserves_allocation(self):
        self.start(order())
        w=replace(self.w,game_loop=246,units=(unit(2,54,passengers=(1,)),))
        self.assertEqual(self.q.tick(w,set()),[])
        self.assertEqual(self.q.tasks[0].program.tags,{1})
        self.assertEqual(self.q.tasks[0].status,'waiting')

    def test_death_and_deadline_report_failed_not_replace_unit(self):
        self.start(order(timeout_s=3))
        self.q.tick(replace(self.w,game_loop=400,units=(unit(3,48),)),set())
        self.assertEqual(self.q.tasks[0].status,'failed')
        self.assertNotIn(3,self.q.protected())

    def test_sieged_tank_unsieges_before_moving(self):
        self.w=replace(self.w,units=(unit(1,32),))
        task=order();task['args']['unit']='siege_tank'
        actions=self.start(task)
        self.assertEqual([a.action_raw.unit_command.ability_id for a in actions],[390])
        self.assertEqual(self.q.tick(replace(self.w,game_loop=246),set()),[])
        actions=self.q.tick(replace(self.w,game_loop=270,units=(unit(1,33),)),set())
        self.assertEqual([a.action_raw.unit_command.ability_id for a in actions],[ATTACK])

    def test_invalid_plan_is_rejected_atomically(self):
        for target in ([float('nan'),1],[500,1],[-1,2],'unknown'):
            with self.assertRaises(PlanValidationError):
                self.q.enqueue([dict(skill='train_worker'),order(target) if isinstance(target,list) else dict(skill='order_units',args={**order()['args'],'target':target})],10)
            self.assertEqual(self.q.tasks,[])
        with self.assertRaises(PlanValidationError):self.q.enqueue([dict(skill='cancel_unit_order',args={'task_id':99})],10)

    def test_expansion_route_excludes_owned_base_and_is_explicit_only(self):
        minerals=[unit(100+i,341,x+dx,30,alliance=3,mineral=True) for x in (30,80,130) for i,dx in enumerate((0,1,2,3))]
        # Unique mineral tags across sites.
        minerals=[replace(u,tag=100+i) for i,u in enumerate(minerals)]
        self.w=replace(self.w,units=tuple([self.marine,unit(2,18,is_building=True)]+minerals))
        self.assertEqual(self.q.tick(self.w,set()),[])
        self.start(scout())
        route=self.q.tasks[0].program.route
        self.assertEqual(len(route),2)
        self.assertLess(route[0][0],route[1][0])
        self.assertTrue(all(p[0]>60 for p in route))

    def test_rally_and_movement_each_have_context_micro_cannot_crash(self):
        self.w=replace(self.w,units=(self.marine,unit(2,21,is_building=True)))
        self.q.enqueue([dict(skill='rally_units',args={'producers':'all','target':[45,45],'reason':'保护生产'}),order()],10)
        actions=self.q.tick(self.w,set())
        self.assertEqual(len(actions),2)
        self.assertEqual(len(self.q.action_contexts),2)
        micro=CombatMicro(self.backend,0,lambda *a,**k:None)
        actions,contexts=micro.apply(self.w,actions,self.q.action_contexts,self.q.protected()-{1})
        self.assertEqual(len(actions),len(contexts))
        self.assertIn(1,micro.intents)
        self.assertFalse(micro._eligible(dict(source='task_queue',skill='scout')))

    def test_scan_and_unresolved_rally_do_not_crash(self):
        self.backend.available_abilities=lambda i,tags,**kw:{t:{399,3673} for t in tags}
        orbital=unit(2,132,is_building=True,energy=100,energy_max=200)
        self.w=replace(self.w,units=(self.marine,orbital,unit(3,21,is_building=True)))
        self.q.enqueue([dict(skill='rally_units',args={'producers':'all','target':[45,45],'reason':'防守'}),
            dict(skill='scan',args={'target':'home'})],10)
        actions=self.q.tick(self.w,set())
        self.assertEqual(len(actions),len(self.q.action_contexts))
        self.assertNotIn('NoneType',' '.join(t.reason for t in self.q.tasks))


class SpatialFacts(unittest.TestCase):
    def test_resource_geometry_refreshes_when_observation_changes(self):
        f,b=engine_backend()
        minerals=[unit(100+i,341,60+i,60,alliance=3,mineral=True) for i in range(8)]
        w=world(minerals,10);bf=Battlefield(b,0)
        first=next(a for a in bf.public(w)['areas'] if a['id']=='site_100')
        # The oldest patch disappears; do not keep stale site id or old center.
        later=bf.public(replace(w,game_loop=448,units=tuple(minerals[1:])))
        self.assertFalse(any(a['id']=='site_100' for a in later['areas']))
        new=next(a for a in later['areas'] if a['id']=='site_101')
        self.assertGreater(new['center'][0],first['center'][0])

    def test_visibility_uses_own_y_x_and_does_not_claim_enemy_elimination(self):
        f,b=engine_backend()
        b.homes=[(30,60)]
        layer=np.zeros((200,200),dtype='uint8');layer[50:71,20:41]=2
        w=replace(world([unit(1,48,30,60)],10),visibility=layer)
        bf=Battlefield(b,0);state=bf.public(w)
        own=next(a for a in state['areas'] if a['id']=='home')
        self.assertEqual(own['state'],'fully_visible')
        self.assertEqual(own['last_fully_visible_game_s'],10)
        self.assertEqual(state['match_status'],'ongoing')
        layer=np.ones((200,200),dtype='uint8')
        later=bf.public(replace(w,game_loop=448,visibility=layer))
        own=next(a for a in later['areas'] if a['id']=='home')
        self.assertEqual(own['state'],'fog_or_unexplored')
        self.assertEqual(own['last_fully_visible_game_s'],10)
        self.assertEqual(own['visible_enemy_buildings'],0)
        self.assertIn('does not exclude',later['meaning'])
        self.assertEqual(area_visibility(replace(w,visibility=None),(30,60))['state'],'unavailable')

    def test_schema_advertises_explicit_search_and_disables_on_legacy(self):
        f,b=engine_backend()
        state=Battlefield(b,0).public(world([unit(1,48)],10))
        schema=output_schema({'tech_tree':f.tree.public('terran'),'control_policy':{'spatial_missions_available':True},'battlefield':state})
        variants=schema['anyOf'][0]['properties']['tasks']['items']['anyOf']
        advertised={name:v for v in variants for name in v['properties']['skill']['enum']}
        self.assertEqual(advertised['scout']['properties']['args']['properties']['targets']['anyOf'][0]['enum'],['expansion_sites'])
        self.assertEqual(advertised['scout']['properties']['args']['properties']['stop_on_contact']['type'],'boolean')
        self.assertNotIn('stop_on_contact',advertised['scout']['properties']['args']['required'])
        self.assertIn('args',advertised['scout']['required'])
        q=TaskQueue(b,0,lambda s:None)
        q.enqueue([scout(),order()],10)
        self.assertEqual(len(q.tasks),2)
        disabled=output_schema({'tech_tree':f.tree.public('terran')})
        self.assertNotIn('scout',str(disabled))
        self.assertIn('only runs after YOUR command',SYSTEM)

    def test_scout_feedback_display_and_model_wake(self):
        from arena.commander import Commander
        from llm_output_window import Feed
        from verify_rally_orders_live import Idle
        f,b=engine_backend();b.initial=[world([unit(1,48)],10)]
        with tempfile.TemporaryDirectory() as root:
            c=Commander(b,0,Idle(),opening=False,feedback_dir=root)
            try:
                c.last_request=10
                c.record_feedback('scout_contact',task_id=1,contacts=[{'unit':'Marine','pos':[80,80]}])
                self.assertEqual(c.last_request,10)
                self.assertFalse(c._request_due(11))
                self.assertTrue(c._request_due(16))
                self.assertTrue(c.control_policy['spatial_missions_available'])
            finally:c.stop()
        feed=Feed();feed.consume({'record':'feedback_event','event':{'kind':'scout_contact','game_loop':224,'contacts':[{'unit':'Marine','pos':[80,80]}]}})
        self.assertIn('等待模型',feed.entries[0]['status'])
        self.assertFalse(feed.entries[0]['error'])

    def test_actual_commander_request_contains_spatial_facts_and_feedback(self):
        import threading
        from arena.commander import Commander
        class Capture:
            compact_battle=True
            soft_timeout=5;timeout=10
            def __init__(self):self.done=threading.Event()
            def chat(self,system,prompt):
                self.system=system;self.payload=json.loads(prompt);self.done.set()
                return json.dumps({'tasks':[],'reason':'先根据真实视野重新判断目标'})
        f,b=engine_backend();layer=np.zeros((200,200),dtype='uint8');layer[158:181,158:181]=2
        w=replace(world([unit(1,48,170,170)],10),visibility=layer)
        b.initial=[w];b.register_action_context=lambda *a:None
        client=Capture()
        with tempfile.TemporaryDirectory() as root:
            c=Commander(b,0,client,opening=False,feedback_dir=root)
            try:
                c.record_feedback('scout_contact',task_id=1,contacts=[{'unit':'Hatchery','pos':[80,80]}])
                c.tick(w)
                self.assertTrue(client.done.wait(2))
                payload=client.payload
                enemy=next(a for a in payload['battlefield']['areas'] if a['id']=='enemy_start')
                self.assertEqual(enemy['state'],'fully_visible')
                self.assertEqual(enemy['visible_enemy_buildings'],0)
                self.assertEqual(payload['battlefield']['match_status'],'ongoing')
                self.assertTrue(any(e['kind']=='scout_contact' for e in payload['feedback']['events']))
                self.assertIn('only runs after YOUR command',client.system)
                self.assertEqual(c.tasks.tasks,[])
            finally:c.stop()


if __name__=='__main__':unittest.main()
