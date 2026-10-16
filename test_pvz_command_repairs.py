"""Reproduce observed PvZ command failures without model calls."""
import unittest
from dataclasses import replace
import test_multirace as fixtures
from arena.tasks import TaskQueue, PlanValidationError


class PvZCommandRepairs(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.MultiRace();self.f.setUp()
        self.b=self.f.backend('protoss',(916,))
        self.events=[]
        self.q=TaskQueue(self.b,0,lambda _:None,
            lambda kind,**kw:self.events.append(dict(kind=kind,**kw)))

    def test_cancel_unissued_unpowered_gateway_and_replan_atomically(self):
        self.q.enqueue([dict(skill='build_structure',args=dict(structure='gateway',base='expansion'))],10)
        old=self.q.tasks[0]
        w=self.f.world([self.f.unit(1,59,is_building=True),self.f.unit(2,62,is_building=True)],race='protoss')
        self.assertEqual(self.q.tick(w,set()),[])
        self.assertEqual(old.program.issued,0)
        self.q.enqueue([dict(skill='cancel_task',args=dict(task_id=old.id)),
            dict(skill='train_unit',count=2,args=dict(unit='zealot'))],11)
        self.assertEqual(old.status,'cancelled')
        actions=self.q.tick(replace(w,game_loop=270),set())
        self.assertEqual([a.action_raw.unit_command.ability_id for a in actions],[916])
        self.assertEqual(self.q.pending_stops,[])

    def test_issued_native_purchase_cannot_be_cancelled_or_refunded(self):
        self.q.enqueue([dict(skill='train_unit',args=dict(unit='zealot'))],10)
        w=self.f.world([self.f.unit(2,62,is_building=True)],race='protoss')
        self.q.tick(w,set());old=self.q.tasks[0];pending=list(old.program.pending)
        with self.assertRaises(PlanValidationError):
            self.q.enqueue([dict(skill='cancel_task',args=dict(task_id=old.id))],11)
        self.assertTrue(old.active);self.assertEqual(old.program.pending,pending)
        self.assertEqual(old.program.issued,1)

    def test_rejected_mixed_replan_keeps_unissued_structure(self):
        self.q.enqueue([dict(skill='build_structure',args=dict(structure='gateway',base='expansion'))],10)
        old=self.q.tasks[0]
        with self.assertRaises(PlanValidationError):
            self.q.enqueue([dict(skill='cancel_task',args=dict(task_id=old.id)),dict(skill='invented')],11)
        self.assertTrue(old.active)

    def test_macro_relocation_is_an_issued_native_action_not_cancellable(self):
        q=TaskQueue(self.f.backend('terran'),0,lambda _:None)
        q.enqueue([dict(skill='build_structure',args=dict(structure='barracks_tech_lab'))],10)
        old=q.tasks[0];old.program.relocation={'tag':2,'phase':'lifting'}
        with self.assertRaises(PlanValidationError):q.enqueue([dict(skill='cancel_task',args=dict(task_id=old.id))],11)

    def test_go_rules_distinguish_paid_morph_and_multiple_overlords(self):
        from arena.autonomous_bot import BOT_RULES
        self.assertIn('付费',BOT_RULES)
        self.assertIn('overlord',BOT_RULES)
        from arena.macro import specification
        self.assertEqual(specification(self.f.tree,'zerg','morph_unit',dict(unit='ravager'))['entry']['recipes'][0]['producer'],690)
        self.assertEqual(specification(self.f.tree,'zerg','train_unit',dict(unit='ravager'))['entry']['recipes'][0]['producer'],110)
        q=TaskQueue(self.f.backend('zerg'),0,lambda _:None)
        q.enqueue([dict(skill='morph_unit',count=4,args=dict(unit='ravager'))],0)
        self.assertEqual(q.summary()[0]['workflow']['form_sources'],['ravager_burrowed'])

    def test_macro_cancel_is_in_schema_without_defense_feature(self):
        import jsonschema
        from arena.battle_plans import output_schema
        schema=output_schema(dict(tech_tree=self.f.tree.public('protoss'),control_policy={}))
        jsonschema.validate(dict(tasks=[dict(skill='cancel_task',args=dict(task_id=45))],reason='取消未开工建筑'),schema)

    def test_late_cancel_targets_actual_unissued_macro_without_defense_order(self):
        from arena.battle_plans import capture,revalidate
        self.q.enqueue([dict(skill='build_structure',args=dict(structure='gateway',base='expansion'))],10)
        w=self.f.world([self.f.unit(1,59,is_building=True)],race='protoss')
        before=capture(w,self.q)
        plan=dict(tasks=[dict(skill='cancel_task',args=dict(task_id=1))])
        self.assertEqual(revalidate(plan,before,w,self.q,(30,30),35),[])
        self.q.tasks[0].program.issued=1
        self.assertTrue(revalidate(plan,before,w,self.q,(30,30),35))

    def test_late_cancel_cannot_target_a_task_created_after_the_request(self):
        from arena.battle_plans import capture,revalidate
        w=self.f.world([],race='protoss');before=capture(w,self.q)
        self.q.enqueue([dict(skill='build_structure',args=dict(structure='gateway',base='expansion'))],10)
        self.assertTrue(revalidate(dict(tasks=[dict(skill='cancel_task',args=dict(task_id=1))]),
            before,w,self.q,(30,30),35))

    def test_late_cancel_of_completed_owned_scout_is_noop_not_failed_defense(self):
        import test_spatial_missions as spatial
        from arena.battle_plans import capture,revalidate
        f=spatial.SpatialMissions();f.setUp();f.start(spatial.order())
        before=capture(f.w,f.q);f.q.update(f.q.tasks[0],'completed','ended while model was thinking')
        plan=dict(tasks=[dict(skill='cancel_task',args=dict(task_id=1))])
        self.assertEqual(revalidate(plan,before,f.w,f.q,(30,30),35),[])

    def test_unacknowledged_purchases_reserve_supply_across_stale_frames(self):
        b=self.f.backend('protoss',(1413,1414,979))
        q=TaskQueue(b,0,lambda _:None)
        q.enqueue([dict(skill='train_unit',args=dict(unit='immortal')),
            dict(skill='train_unit',count=2,args=dict(unit='zealot')),
            dict(skill='train_unit',count=2,args=dict(unit='stalker'))],10)
        w=replace(self.f.world([self.f.unit(1,71,is_building=True),
            *[replace(self.f.unit(2+i,133,is_building=True),x=40+i*6) for i in range(4)],
            self.f.unit(10,59,is_building=True),replace(self.f.unit(11,60,is_building=True),y=45)],race='protoss'),supply_used=12,supply_cap=21,
            minerals=1000,vespene=1000)
        first=q.tick(w,set());self.assertEqual(len(first),3)
        # Real-time observations may lag actions. The remaining 1 food is not 9 again.
        self.assertEqual(q.tick(replace(w,game_loop=250),set()),[])
        self.assertEqual(q.pending_purchase_reservations()['supply'],8)
        self.assertEqual([t.program.issued for t in q.tasks],[1,1,1])

    def test_unacknowledged_purchase_reserves_minerals_but_rejection_releases_it(self):
        b=self.f.backend('protoss',(1413,));q=TaskQueue(b,0,lambda _:None)
        q.enqueue([dict(skill='train_unit',count=2,args=dict(unit='zealot'))],10)
        w=replace(self.f.world([self.f.unit(1,133,is_building=True),
            replace(self.f.unit(2,133,is_building=True),x=40),self.f.unit(3,59,is_building=True),
            replace(self.f.unit(4,60,is_building=True),y=45)],
            race='protoss'),minerals=100)
        self.assertEqual(len(q.tick(w,set())),1)
        self.assertEqual(q.tick(replace(w,game_loop=250),set()),[])
        self.assertEqual(q.pending_purchase_reservations()['minerals'],100)
        rejected=replace(w,game_loop=280,errors=((1,1413,9),))
        q.tick(rejected,set())
        self.assertEqual(q.pending_purchase_reservations()['minerals'],0)

    def test_observed_warp_phase_releases_hold_without_claiming_completion(self):
        b=self.f.backend('protoss',(1413,));q=TaskQueue(b,0,lambda _:None)
        q.enqueue([dict(skill='train_unit',count=2,args=dict(unit='zealot'))],10)
        w=replace(self.f.world([self.f.unit(1,133,is_building=True),
            replace(self.f.unit(4,60,is_building=True),y=45)],race='protoss'),minerals=100)
        q.tick(w,set());pos=q.tasks[0].program.pending[0]['pos']
        phase=replace(self.f.unit(5,73),x=pos[0],y=pos[1],progress=.5)
        q.tick(replace(w,game_loop=250,minerals=0,units=w.units+(phase,)),set())
        self.assertEqual(q.pending_purchase_reservations()['minerals'],0)
        self.assertEqual(q.tasks[0].program.born,set())
        self.assertTrue(q.tasks[0].active)


if __name__=='__main__':unittest.main()
