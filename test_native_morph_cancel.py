"""Bound building-morph cancellation, atomic ownership and truthful native evidence."""
import unittest
from dataclasses import replace
from arena.tasks import TaskQueue,PlanValidationError
from arena.native_morph_cancel import supported,report
from arena.battle_plans import output_schema,capture,revalidate
import test_multirace as fixtures


class MorphCancel(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.MultiRace();self.f.setUp();self.events=[]
        self.b=self.f.backend('zerg',[3659]);self.q=TaskQueue(self.b,0,lambda _:None,
            lambda kind,**kw:self.events.append(dict(kind=kind,**kw)))
        self.q.enqueue([dict(skill='build_structure',args=dict(structure='lair'))],0)
        self.original=self.q.tasks[0];run=self.original.program
        self.ability=run.spec['entry']['recipes'][0]['ability']
        run.issued=1;run.baseline=set();run.tags={1}
        run.pending=[dict(tags=[1],ability=self.ability,sent=0,seen=True,minerals=150,gas=100,supply=0)]
        self.hatch=self.f.unit(1,86,is_building=True,orders=(self.ability,))
        self.w=self.f.world([self.hatch],race='zerg')
    def cancel(self):
        self.q.enqueue([dict(skill='cancel_native_morph',args=dict(task_id=self.original.id))],self.w.seconds)
        return self.q.tasks[-1]
    def test_native_morph_order_cancel_is_single_and_requires_fresh_observed_end(self):
        task=self.cancel();actions=self.q.tick(self.w,set());self.assertEqual(len(actions),1)
        self.assertEqual(list(actions[0].action_raw.unit_command.unit_tags),[1])
        self.assertEqual(self.q.tick(replace(self.w,game_loop=250),set()),[])
        after=replace(self.w,units=(replace(self.hatch,orders=()),),game_loop=300)
        self.assertEqual(self.q.tick(after,set()),[])
        self.assertEqual(task.status,'completed');self.assertEqual(self.original.status,'cancelled')
        self.assertEqual(task.program.dispatches,1)
        self.assertFalse(task.program.summary()['refund_confirmed'])
        self.assertEqual(self.q.tick(replace(after,game_loop=500),set()),[])
    def test_incomplete_new_form_without_a_raw_order_can_revert_to_original_tag(self):
        self.w=replace(self.w,units=(replace(self.hatch,type_id=100,progress=.5,orders=()),))
        task=self.cancel();self.assertEqual(len(self.q.tick(self.w,set())),1)
        self.q.tick(replace(self.w,units=(replace(self.hatch,orders=()),),game_loop=300),set())
        self.assertEqual(task.status,'completed')

    def test_macro_retains_the_paid_upgrade_binding_when_the_new_form_has_no_order(self):
        self.w=replace(self.w,units=(replace(self.hatch,type_id=100,progress=.5,orders=()),))
        self.assertEqual(self.q.tick(self.w,set()),[])
        self.assertEqual(self.original.program.pending[0]['building_tag'],1)
        self.assertEqual(self.original.program.pending[0]['tags'],[1])
        task=self.cancel();self.assertEqual(len(self.q.tick(self.w,set())),1)
        self.q.tick(replace(self.w,units=(replace(self.hatch,orders=()),),game_loop=300),set())
        self.assertEqual(task.status,'completed')
    def test_rejection_foreign_queue_and_lost_owner_never_claim_success(self):
        for case in ('rejection','foreign_order','lost_owner'):
            self.setUp();task=self.cancel()
            if case=='foreign_order':self.w=replace(self.w,units=(replace(self.hatch,orders=(1342,)),))
            if case=='lost_owner':self.w=replace(self.w,units=(replace(self.hatch,alliance=4),))
            actions=self.q.tick(self.w,set())
            if case=='rejection':
                self.assertEqual(len(actions),1)
                self.q.tick(replace(self.w,game_loop=300,errors=((1,3659,2),)),set())
            else:self.assertEqual(actions,[])
            self.assertEqual(task.status,'failed');self.assertEqual(self.original.status,'cancelled')
            self.assertFalse(getattr(self.original,'native_morph_cancellation_completed',False))
    def test_completed_morph_is_kept_and_unissued_goals_are_honest_noops(self):
        for case in ('completed','unissued'):
            self.setUp()
            if case=='completed':self.w=replace(self.w,units=(replace(self.hatch,type_id=100,orders=()),))
            else:self.original.program.issued=0;self.original.program.pending=[]
            task=self.cancel();self.assertEqual(self.q.tick(self.w,set()),[])
            self.assertEqual(task.status,'completed');self.assertEqual(task.program.dispatches,0)
    def test_only_paid_same_building_upgrades_are_supported_and_validation_is_atomic(self):
        from arena.macro import MacroRun,specification
        from types import SimpleNamespace
        for name in ('lair','hive','orbital_command','planetary_fortress','greater_spire'):
            race=self.f.tree.units[name]['race']
            task=SimpleNamespace(program=MacroRun(specification(self.f.tree,race,'build_structure',dict(structure=name)),{}))
            self.assertTrue(supported(task,self.f.tree))
        with self.assertRaises(PlanValidationError):self.q.enqueue([
            dict(skill='cancel_native_morph',args=dict(task_id=1)),dict(skill='invented')],0)
        self.assertTrue(self.original.active)
        for args in ({'task_id':999},{'task_id':True},{'task_id':1,'refund':True}):
            with self.assertRaises(PlanValidationError):self.q.enqueue([dict(skill='cancel_native_morph',args=args)],0)
        for goal in (dict(skill='train_worker'),dict(skill='build_supply')):
            self.q.enqueue([goal],0);task=self.q.tasks[-1]
            self.assertFalse(supported(task,self.f.tree))
            with self.assertRaises(PlanValidationError):self.q.enqueue([dict(skill='cancel_native_morph',args=dict(task_id=task.id))],0)
    def test_schema_and_late_plans_require_original_owned_task_identity(self):
        schema=output_schema(dict(native_morph_cancellation=report(self.q)))['anyOf'][0]
        self.assertTrue(any(v['properties']['skill']['enum']==['cancel_native_morph']
            for v in schema['properties']['tasks']['items']['anyOf']))
        before=capture(self.w,self.q)
        self.q.enqueue([dict(skill='build_structure',args=dict(structure='hive'))],0)
        plan=dict(tasks=[dict(skill='cancel_native_morph',args=dict(task_id=self.q.tasks[-1].id))])
        self.assertTrue(revalidate(plan,before,replace(self.w,game_loop=1000),self.q,(30,30),35))

    def test_issued_unbound_morph_never_claims_native_cancellation(self):
        self.original.program.pending=[];self.original.program.tags=set()
        task=self.cancel();self.assertEqual(self.q.tick(self.w,set()),[])
        self.assertEqual(task.status,'failed')
        self.assertFalse(getattr(self.original,'native_morph_cancellation_completed',False))


if __name__=='__main__':unittest.main()
