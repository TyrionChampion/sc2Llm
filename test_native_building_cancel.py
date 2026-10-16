"""Macro lifecycle and native building cancellation; no game/API calls."""
import unittest
from dataclasses import replace
from arena.tasks import TaskQueue,PlanValidationError
from arena.battle_plans import output_schema,capture,revalidate
from arena.native_building_cancel import report
import test_multirace as fixtures


class BuildingCancel(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.MultiRace();self.f.setUp();self.tree=self.f.tree
        self.backend=self.f.backend('protoss',[3659])
        self.events=[];self.q=TaskQueue(self.backend,0,lambda _:None,lambda kind,**data:self.events.append(dict(kind=kind,**data)))
        self.q.enqueue([dict(skill='build_structure',args=dict(structure='pylon'))],0)
        self.original=self.q.tasks[-1];run=self.original.program;run.baseline={10};run.issued=1
        self.ability=run.spec['entry']['recipes'][0]['ability']
        run.pending=[dict(tags=[2],pos=[40,40],ability=self.ability,seen=False,minerals=100,gas=0,supply=0)]
        run.tags={2}
        self.worker=self.f.unit(2,84,orders=(self.ability,),order_positions=((40,40),))
        self.existing=replace(self.f.unit(10,60,is_building=True),x=20,y=20)
        self.building=replace(self.f.unit(3,60,is_building=True,progress=.2),x=40,y=40)
        self.w=self.f.world([self.worker,self.existing,self.building],race='protoss')
    def cancel(self):
        self.q.enqueue([dict(skill='cancel_native_building',args=dict(task_id=self.original.id))],self.w.seconds,request_id=7)
        return self.q.tasks[-1]
    def test_stops_macro_and_cancels_only_linked_unfinished_building_once(self):
        task=self.cancel()
        self.assertEqual(self.original.status,'cancelled')
        self.assertFalse(self.original.native_cancellation_completed if hasattr(self.original,'native_cancellation_completed') else False)
        actions=self.q.tick(self.w,set())
        self.assertEqual(len(actions),1)
        raw=actions[0].action_raw.unit_command
        self.assertEqual(list(raw.unit_tags),[3])
        self.assertEqual(self.tree.canonical_ability(raw.ability_id),3659)
        self.assertEqual(self.q.tick(replace(self.w,game_loop=225),set()),[])
        after=replace(self.w,game_loop=226,units=(self.worker,self.existing))
        self.q.tick(after,set())
        self.assertEqual(task.status,'completed')
        self.assertTrue(self.original.native_cancellation_completed)
        event=next(e for e in self.events if e['kind']=='native_building_cancel_observed')
        self.assertFalse(event['refund_confirmed'])
        self.assertEqual(self.q.tick(after,set()),[])
    def test_native_rejection_does_not_rebuy_or_claim_refund(self):
        task=self.cancel();self.q.tick(self.w,set())
        self.q.tick(replace(self.w,game_loop=225,errors=((3,3659,2),)),set())
        self.assertEqual(task.status,'failed');self.assertEqual(self.original.status,'cancelled')
        self.assertFalse(getattr(self.original,'native_cancellation_completed',False))
        self.assertEqual(sum(e['kind']=='native_building_cancel_order_issued' for e in self.events),1)
    def test_builder_approach_stops_before_native_building_appears(self):
        self.w=replace(self.w,units=(self.worker,self.existing))
        task=self.cancel();actions=self.q.tick(self.w,set())
        self.assertEqual((actions[0].action_raw.unit_command.ability_id,list(actions[0].action_raw.unit_command.unit_tags)),(4,[2]))
        self.q.tick(replace(self.w,game_loop=225,units=(replace(self.worker,orders=()),self.existing)),set())
        self.assertEqual(task.status,'completed')
    def test_builder_to_building_race_does_not_claim_success(self):
        self.w=replace(self.w,units=(self.worker,self.existing))
        task=self.cancel();self.q.tick(self.w,set())
        self.q.tick(replace(self.w,game_loop=225,units=(self.worker,self.existing,self.building)),set())
        self.assertEqual(task.status,'failed')
        self.assertFalse(getattr(self.original,'native_cancellation_completed',False))

    def test_builder_reassigned_to_same_product_elsewhere_is_not_stopped(self):
        self.w=replace(self.w,units=(replace(self.worker,order_positions=((65,65),)),self.existing))
        task=self.cancel();self.assertEqual(self.q.tick(self.w,set()),[])
        self.assertEqual(self.q.tick(replace(self.w,game_loop=int(45*22.4)),set()),[])
        self.assertEqual(task.status,'failed')
        self.assertFalse(any(e['kind']=='native_building_cancel_order_issued' for e in self.events))
    def test_cancelled_zerg_building_reverting_to_worker_with_same_tag_is_observed(self):
        task=self.cancel();self.q.tick(self.w,set())
        reverted=replace(self.building,type_id=104,is_building=False,progress=1)
        self.q.tick(replace(self.w,game_loop=225,units=(self.worker,self.existing,reverted)),set())
        self.assertEqual(task.status,'completed')
    def test_completed_building_and_unissued_goal_are_honest_noops(self):
        for case in ('already_done','unissued'):
            self.setUp()
            if case=='already_done':self.w=replace(self.w,units=(self.worker,self.existing,replace(self.building,progress=1)))
            else:self.original.program.issued=0;self.original.program.pending=[]
            task=self.cancel();self.assertEqual(self.q.tick(self.w,set()),[])
            self.assertEqual(task.status,'completed')
            self.assertFalse(next(e for e in self.events if e['kind']=='native_building_cancel_observed')['native_order_dispatched'])
    def test_gateway_autoconversion_does_not_cancel_the_later_free_morph(self):
        self.original.program.spec['entry']=self.tree.units['gateway']
        self.w=replace(self.w,units=(self.worker,self.existing,replace(self.building,type_id=133,progress=.5)))
        task=self.cancel();self.assertEqual(self.q.tick(self.w,set()),[])
        self.assertEqual(task.status,'completed')
        self.assertIn('converted',task.reason)
    def test_failed_macro_can_still_cancel_its_live_unfinished_building(self):
        self.original.status='failed';task=self.cancel()
        self.assertEqual(len(self.q.tick(self.w,set())),1)
        self.assertEqual(task.status,'running')
    def test_ambiguous_missing_foreign_or_unavailable_targets_never_cancel_other_buildings(self):
        for case in ('ambiguous','missing','foreign','unavailable'):
            self.setUp()
            if case=='ambiguous':self.w=replace(self.w,units=self.w.units+(replace(self.building,tag=4),))
            if case in ('missing','foreign'):
                self.w=replace(self.w,units=(replace(self.worker,orders=()),self.existing,
                    *([replace(self.building,alliance=4)] if case=='foreign' else [])))
            if case=='unavailable':self.backend.available_abilities=lambda i,tags,**kw:{t:set() for t in tags}
            task=self.cancel();self.assertEqual(self.q.tick(self.w,set()),[])
            self.q.tick(replace(self.w,game_loop=int(45*22.4)),set())
            self.assertEqual(task.status,'failed')
    def test_mixed_invalid_plan_does_not_stop_old_goal(self):
        with self.assertRaises(PlanValidationError):self.q.enqueue([
            dict(skill='cancel_native_building',args=dict(task_id=self.original.id)),dict(skill='invented')],0)
        self.assertTrue(self.original.active);self.assertEqual(len(self.q.tasks),1)
    def test_unit_queue_research_upgrade_and_unknown_ids_are_rejected_atomically(self):
        self.q.enqueue([dict(skill='train_worker')],0);worker=self.q.tasks[-1]
        for task_id in (worker.id,999):
            with self.assertRaises(PlanValidationError):self.q.enqueue([dict(skill='cancel_native_building',args=dict(task_id=task_id))],0)
        for bad in ({'task_id':True},{'task_id':1,'refund':True}):
            with self.assertRaises(PlanValidationError):self.q.enqueue([dict(skill='cancel_native_building',args=bad)],0)
        self.assertTrue(worker.active);self.assertTrue(self.original.active)
    def test_no_refund_or_cancel_success_is_inferred_while_pending(self):
        task=self.cancel()
        self.assertEqual(self.q.pending_purchase_reservations()['minerals'],100)
        self.q.tick(self.w,set())
        self.assertEqual(task.status,'running')
        self.assertEqual(self.q.pending_purchase_reservations()['minerals'],0)
        self.assertFalse(task.program.summary()['refund_confirmed'])
    def test_repeat_after_confirmed_cancel_is_noop(self):
        self.cancel();self.q.tick(self.w,set())
        after=replace(self.w,units=(self.worker,self.existing),game_loop=225)
        self.q.tick(after,set())
        task=self.cancel();self.assertEqual(self.q.tick(after,set()),[])
        self.assertEqual(task.status,'completed')
    def test_schema_and_late_scope_preserve_original_ownership(self):
        data=report(self.q);self.assertEqual(data['supported_tasks'][0]['task_id'],1)
        schema=output_schema(dict(native_building_cancellation=data))['anyOf'][0]
        branch=next(v for v in schema['properties']['tasks']['items']['anyOf'] if v['properties']['skill']['enum']==['cancel_native_building'])
        self.assertIn('args',branch['required'])
        old=capture(self.w,self.q)
        self.q.enqueue([dict(skill='build_structure',args=dict(structure='gateway'))],0)
        issue=revalidate(dict(tasks=[dict(skill='cancel_native_building',args=dict(task_id=self.q.tasks[-1].id))]),
            old,replace(self.w,game_loop=1000),self.q,(30,30),35)
        self.assertTrue(issue)


if __name__=='__main__':unittest.main()
