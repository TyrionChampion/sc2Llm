"""Direct ability dispatch/observation contracts; no live engine or API requests."""
import unittest
from dataclasses import replace

from arena.tasks import TaskQueue,PlanValidationError
from arena.native_abilities import report,validate_args
from arena.battle_plans import output_schema,capture,revalidate
import test_multirace as fixtures


class NativeAbilities(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.MultiRace();self.f.setUp();self.tree=self.f.tree
        self.b=self.f.backend('protoss',[1442,76,3755])
        self.events=[];self.q=TaskQueue(self.b,0,lambda _:None,lambda kind,**data:self.events.append(dict(kind=kind,**data)))
        self.caster=self.f.unit(1,74)
        self.w=self.f.world([self.caster],race='protoss')
    def enqueue(self,ability='effect_blink_stalker',target=None,caster=1,**extra):
        self.q.enqueue([dict(skill='use_ability',args=dict(caster=caster,ability=ability,
                         target=[33,30] if target is None else target,**extra))],self.w.seconds)
    def test_point_cast_issues_once_and_waits_for_acknowledgement(self):
        self.enqueue();actions=self.q.tick(self.w,set())
        self.assertEqual(len(actions),1)
        raw=actions[0].action_raw.unit_command
        self.assertEqual((raw.ability_id,list(raw.unit_tags)),(1442,[1]))
        self.assertEqual((raw.target_world_space_pos.x,raw.target_world_space_pos.y),(33,30))
        self.assertEqual(self.q.tick(replace(self.w,game_loop=225),set()),[])
        self.assertEqual(self.q.tasks[0].status,'running')
        self.b.available_abilities=lambda i,tags,**kw:{t:set() for t in tags}
        self.q.tick(replace(self.w,game_loop=226),set())
        self.assertEqual(self.q.tasks[0].status,'completed')
        ack=next(e for e in self.events if e['kind']=='native_ability_acknowledged')
        self.assertFalse(ack['effect_confirmed'])
        self.assertEqual(self.q.tick(self.w,set()),[])
    def test_energy_and_order_acknowledgements_do_not_claim_effects(self):
        for evidence in ('energy','order'):
            self.setUp();self.caster=self.f.unit(1,77,energy=100)
            self.w=self.f.world([self.caster],race='protoss')
            self.q.enqueue([dict(skill='use_ability',args=dict(caster=1,ability='effect_guardian_shield',target=None))],self.w.seconds)
            self.assertEqual(len(self.q.tick(self.w,set())),1)
            changed=replace(self.caster,energy=25) if evidence=='energy' else replace(self.caster,orders=(76,))
            self.q.tick(replace(self.w,units=(changed,),game_loop=225),set())
            self.assertEqual(self.q.tasks[0].status,'completed')
            self.assertFalse(next(e for e in self.events if e['kind']=='native_ability_acknowledged')['effect_confirmed'])
    def test_same_ability_order_for_another_unit_target_is_not_this_requests_ack(self):
        q=TaskQueue(self.f.backend('zerg',[251]),0,lambda _:None)
        queen=self.f.unit(1,126,energy=70)
        bases=[self.f.unit(i,86,is_building=True) for i in (2,3)]
        w=self.f.world([queen,*bases],race='zerg')
        q.enqueue([dict(skill='use_ability',args=dict(caster=1,ability='effect_inject_larva',target=3))],w.seconds)
        self.assertEqual(len(q.tick(w,set())),1)
        old=replace(queen,orders=(251,),order_targets=(2,))
        q.tick(replace(w,units=(old,*bases),game_loop=225),set())
        self.assertEqual(q.tasks[0].status,'running')
        q.tick(replace(w,units=(replace(old,order_targets=(3,)),*bases),game_loop=226),set())
        self.assertEqual(q.tasks[0].status,'completed')
    def test_generic_inject_waits_for_an_old_native_injection_to_end(self):
        q=TaskQueue(self.f.backend('zerg',[251]),0,lambda _:None)
        queen=self.f.unit(1,126,energy=70,orders=(251,),order_targets=(2,))
        bases=[self.f.unit(i,86,is_building=True) for i in (2,3)]
        w=self.f.world([queen,*bases],race='zerg')
        q.enqueue([dict(skill='use_ability',args=dict(caster=1,ability='effect_inject_larva',target=3))],w.seconds)
        self.assertEqual(q.tick(w,set()),[])
        cleared=replace(queen,orders=(),order_targets=())
        out=q.tick(replace(w,units=(cleared,*bases),game_loop=247),set())
        self.assertEqual(len(out),1);self.assertEqual(out[0].action_raw.unit_command.target_unit_tag,3)
    def test_point_retarget_dispatches_but_old_point_does_not_acknowledge_new_goal(self):
        q=TaskQueue(self.f.backend('terran',[23]),0,lambda _:None)
        actor=self.f.unit(1,48,orders=(23,),order_positions=((33,30),))
        w=self.f.world([actor],race='terran')
        q.enqueue([dict(skill='use_ability',args=dict(caster=1,ability='attack_attack',target=[80,80]))],w.seconds)
        self.assertEqual(len(q.tick(w,set())),1)
        q.tick(replace(w,game_loop=225),set());self.assertEqual(q.tasks[0].status,'running')
        q.tick(replace(w,units=(replace(actor,order_positions=((80,80),)),),game_loop=226),set())
        self.assertEqual(q.tasks[0].status,'completed')
    def test_explicit_inject_retarget_does_not_wait_out_an_old_distant_approach(self):
        q=TaskQueue(self.f.backend('zerg',[251]),0,lambda _:None)
        queen=self.f.unit(1,126,energy=70,orders=(251,),order_targets=(2,))
        far=replace(self.f.unit(2,86,is_building=True),x=100)
        home=self.f.unit(3,86,is_building=True)
        w=self.f.world([queen,far,home],race='zerg')
        q.enqueue([dict(skill='use_ability',args=dict(caster=1,ability='effect_inject_larva',target=3))],w.seconds)
        out=q.tick(w,set());self.assertEqual(len(out),1)
        self.assertEqual(out[0].action_raw.unit_command.target_unit_tag,3)
    def test_hidden_target_lost_caster_and_engine_rejection_fail_without_recast(self):
        for case in ('target','caster','rejection'):
            self.setUp()
            if case=='target':self.enqueue('attack_attack',99)
            else:self.enqueue()
            if case=='caster':self.w=replace(self.w,units=())
            if case=='rejection':self.w=replace(self.w,errors=((1,1442,3),))
            self.assertEqual(self.q.tick(self.w,set()),[])
            self.assertEqual(self.q.tasks[0].status,'failed')
            self.assertFalse(any(e['kind']=='native_ability_order_issued' for e in self.events))
    def test_reserved_actor_and_unavailable_ability_wait_until_bounded_deadline(self):
        for case in ('reserved','unavailable'):
            self.setUp();self.enqueue(timeout_s=2)
            if case=='reserved':self.q.defense_tags={1}
            else:self.b.available_abilities=lambda i,tags,**kw:{t:set() for t in tags}
            self.assertEqual(self.q.tick(self.w,set()),[])
            self.assertEqual(self.q.tasks[0].status,'waiting')
            self.q.tick(replace(self.w,game_loop=self.w.game_loop+67),set())
            self.assertEqual(self.q.tasks[0].status,'failed')
    def test_unacknowledged_cast_expires_without_automatic_retry(self):
        self.enqueue(timeout_s=2);self.assertEqual(len(self.q.tick(self.w,set())),1)
        self.q.tick(replace(self.w,game_loop=self.w.game_loop+67),set())
        self.assertEqual(self.q.tasks[0].status,'failed')
        self.assertEqual(self.q.tasks[0].attempts,0)
        self.assertEqual(sum(e['kind']=='native_ability_order_issued' for e in self.events),1)
    def test_invalid_parameters_and_paid_actions_reject_entire_reply(self):
        base=dict(caster=1,ability='effect_blink_stalker',target=[33,30])
        for delta in ({'target':[float('nan'),30]},{'target':[200,30]},{'target':True},
                      {'caster':True},{'ability':'invented'},{'ability':'train_stalker'},
                      {'timeout_s':121},{'timeout_s':float('inf')},{'unexpected':1}):
            with self.subTest(delta=delta),self.assertRaises(PlanValidationError):
                self.q.enqueue([{'skill':'train_worker'},dict(skill='use_ability',args=base|delta)],0)
            self.assertEqual(self.q.tasks,[])
    def test_duplicate_caster_and_count_are_atomic_rejections(self):
        args=dict(caster=1,ability='effect_blink_stalker',target=[33,30])
        for tasks in ([dict(skill='use_ability',args=args,count=2)],
                      [dict(skill='use_ability',args=args),dict(skill='use_ability',args=args|{'target':[34,30]})]):
            with self.assertRaises(PlanValidationError):self.q.enqueue(tasks,0)
            self.assertEqual(self.q.tasks,[])
    def test_replayed_request_never_creates_a_second_cast_after_completion(self):
        task=dict(skill='use_ability',args=dict(caster=1,ability='effect_blink_stalker',target=[33,30]))
        self.q.enqueue([task],0,request_id=7)
        self.assertEqual(len(self.q.tick(self.w,set())),1)
        self.b.available_abilities=lambda i,tags,**kw:{t:set() for t in tags}
        self.q.tick(replace(self.w,game_loop=225),set())
        self.assertEqual(self.q.tasks[0].status,'completed')
        self.q.enqueue([task],1,request_id=7)
        self.assertEqual(len(self.q.tasks),1)
        self.assertEqual(self.q.tick(self.w,set()),[])
        self.assertTrue(any(e['kind']=='native_ability_request_replayed' for e in self.events))
    def test_busy_nexus_can_chrono_boost_without_stealing_production(self):
        nexus=self.f.unit(1,59,is_building=True,energy=100,orders=(1006,))
        gateway=self.f.unit(2,62,is_building=True)
        self.w=self.f.world([nexus,gateway],race='protoss')
        self.q.enqueue([dict(skill='use_ability',args=dict(caster=1,ability='effect_chrono_boost_energy_cost',target=2))],0)
        actions=self.q.tick(self.w,{1})
        self.assertEqual(len(actions),1)
        self.assertEqual(actions[0].action_raw.unit_command.target_unit_tag,2)
        self.assertEqual(nexus.orders,(1006,))
    def test_report_has_owned_casters_current_targets_and_live_cargo_commands(self):
        # A live query is authoritative even when cargo-dependent commands
        # are missing in the static reference list.
        unload=next(a for a,name in self.tree.ability_names.items() if name=='unload_all_at_medivac')
        medivac=self.f.unit(1,54,passengers=(9,))
        hidden_enemy=replace(self.f.unit(3,48),alliance=4)
        self.b.available_abilities=lambda i,tags,**kw:{t:{unload} for t in tags}
        self.w=self.f.world([medivac,hidden_enemy],race='terran')
        d=report(self.w,self.q)
        self.assertEqual([r[0] for r in d['actors']['rows']],[1])
        self.assertIn(unload,d['actors']['rows'][0][-1])
        self.assertIn(str(unload),d['catalogue'])
        self.assertEqual(d['targets']['rows'][0][0],3)
    def test_schema_requires_arguments_and_late_cast_requires_fresh_observation(self):
        d=report(self.w,self.q)
        schema=output_schema(dict(native_abilities=d))
        branch=next(b for b in schema['anyOf'][0]['properties']['tasks']['items']['anyOf'] if b['properties']['skill']['enum']==['use_ability'])
        self.assertIn('args',branch['required'])
        issues=revalidate(dict(tasks=[dict(skill='use_ability',args=dict(caster=1,ability='effect_blink_stalker',target=[33,30]))]),
            capture(self.w,self.q),replace(self.w,game_loop=self.w.game_loop+784),self.q,(30,30),35)
        self.assertTrue(issues)
    def test_legacy_backend_does_not_fabricate_ability_support(self):
        with self.assertRaises(ValueError):validate_args(None,{},(200,200))
    def test_failed_native_query_reports_unknown_without_fabricating_readiness(self):
        def failed(*args,**kwargs):raise RuntimeError('incomplete ability query response')
        self.b.available_abilities=failed
        data=report(self.w,self.q)
        self.assertFalse(data['availability_observed'])
        self.assertEqual(data['query_error_type'],'RuntimeError')
        self.assertIsNone(data['actors']['rows'][0][-1])
        self.assertEqual(data['actors']['rows'][0][0],1)
    def test_coarse_terrain_distinguishes_empty_corner_without_claiming_paths(self):
        import numpy as np
        from types import SimpleNamespace
        pathing=np.zeros((16,16),dtype=np.uint8);pathing[1,1]=1
        self.q._rally_grid=lambda:SimpleNamespace(pathing=pathing,w=16,h=16)
        data=report(self.w,self.q)['map']
        self.assertTrue(data['pathability_available'])
        self.assertEqual(data['walkable_cells'],['10','00'])
        self.assertIn('not unit occupancy',data['meaning'])
    def test_native_queue_cancellation_is_not_an_untracked_spell(self):
        from arena.native_abilities import permitted
        for ability in (314,3659,3671,3672):
            self.assertFalse(permitted(self.tree,ability))
    def test_large_current_ability_report_survives_prompt_compaction(self):
        import json
        from arena.autonomous_bot import BotPlanner,RequestBudget,BOT_RULES
        from arena.context_tables import decode_tables
        from test_autonomous_bot import Client
        units=[self.f.unit(n+1,(84,73,74,77,83,59)[n%6]) for n in range(120)]
        self.w=self.f.world(units,race='protoss')
        data=report(self.w,self.q)
        payload=dict(world=self.w.summary(),tech_tree=self.tree.public('protoss'),
                     native_abilities=data,feedback={'events':[{'kind':'actual failure','message':'retain me'}]})
        planner=BotPlanner(Client(),RequestBudget(2),'own strategy')
        planner.chat('',json.dumps(payload))
        decoded=decode_tables(planner.client.payload)
        self.assertEqual(decoded['native_abilities'],data)
        self.assertEqual(decoded['feedback'],payload['feedback'])
        self.assertLessEqual(len(BOT_RULES)+len(json.dumps(planner.client.payload,ensure_ascii=False,separators=(',',':'))),45000)
    def test_hallucinated_troops_do_not_complete_a_paid_training_goal(self):
        self.q.enqueue([dict(skill='train_unit',args=dict(unit='stalker'))],0)
        task=self.q.tasks[-1];task.program.baseline=set();task.program.issued=1
        task.program.pending=[dict(tags=[10],ability=1414,pos=[35,35],sent=10,seen=False,warp_in=True,ack_timeout_s=30)]
        gate=self.f.unit(10,133,is_building=True)
        fake=replace(self.f.unit(11,74,hallucination=True),x=35,y=35)
        self.q.tick(self.f.world([gate,fake],race='protoss'),set())
        self.assertNotEqual(task.status,'completed');self.assertEqual(task.program.born,set())
        real=replace(fake,hallucination=False)
        self.q.tick(self.f.world([gate,real],race='protoss'),set())
        self.assertEqual(task.status,'completed')
    def test_gateway_autoconversion_and_existing_warpgate_baseline(self):
        self.q.enqueue([dict(skill='build_structure',args=dict(structure='gateway'))],0)
        task=self.q.tasks[-1]
        existing=self.f.unit(10,133,is_building=True)
        self.q.tick(self.f.world([existing],race='protoss'),set())
        self.assertIn(10,task.program.baseline)
        self.assertNotEqual(task.status,'completed')
        task.program.issued=1
        task.program.pending=[dict(tags=[20],pos=[40,40],
            ability=task.program.spec['entry']['recipes'][0]['ability'],seen=True,sent=0)]
        converted=replace(self.f.unit(12,133,is_building=True),x=40,y=40)
        self.q.tick(self.f.world([existing,converted],race='protoss'),set())
        self.assertEqual(task.status,'completed')
        self.assertEqual(task.program.born,{12})
    def test_new_units_before_dispatch_do_not_complete_a_purchase(self):
        self.q.enqueue([dict(skill='train_unit',args=dict(unit='stalker'))],0)
        task=self.q.tasks[-1];task.program.baseline=set()
        self.q.tick(self.f.world([self.f.unit(11,74)],race='protoss'),set())
        self.assertEqual(task.program.issued,0)
        self.assertEqual(task.program.born,set())
        self.assertNotEqual(task.status,'completed')
    def test_suppressed_dispatch_does_not_reserve_a_purchase_or_claim_issued_quantity(self):
        from unittest.mock import Mock
        backend=self.f.backend('terran',[524]);q=TaskQueue(backend,0,lambda _:None)
        q.enqueue([dict(skill='train_worker')],0)
        q.issue=Mock(return_value=False)
        actions=q.tick(self.f.world([self.f.unit(1,18,is_building=True)],race='terran'),set())
        self.assertEqual(actions,[])
        self.assertEqual(q.tasks[0].program.issued,0)
        self.assertEqual(q.tasks[0].program.pending,[])
        self.assertEqual(q.pending_purchase_reservations()['minerals'],0)
    def test_reverse_gateway_morph_does_not_accept_unchanged_warpgate(self):
        from arena.macro import MacroRun,specification
        spec=specification(self.tree,'protoss','morph_unit',dict(unit='gateway'))
        run=MacroRun(spec,dict(unit='gateway'))
        existing=self.f.unit(10,133,is_building=True)
        self.assertEqual(run.product_units(self.f.world([existing],race='protoss')),[])


if __name__=='__main__':unittest.main()
