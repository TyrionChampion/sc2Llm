"""Stop only future purchases, preserving paid eggs, quantities and explicit append semantics."""
from dataclasses import replace
import unittest
import jsonschema

from arena.battle_plans import capture,revalidate,output_schema
from arena.tasks import TaskQueue,PlanValidationError
import test_multirace as fixtures


class StopProduction(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.MultiRace();self.f.setUp();self.q=TaskQueue(self.f.backend('zerg',[1343]),0,lambda _:None)
        self.w=self.f.world([self.f.unit(1,86,is_building=True),self.f.unit(2,151),
            self.f.unit(3,151),self.f.unit(4,89,is_building=True)],race='zerg')
        self.q.enqueue([dict(skill='train_unit',count=20,args=dict(unit='zergling'))],self.w.seconds)
        self.task=self.q.tasks[0]

    def stop(self):self.q.enqueue([dict(skill='stop_production',args=dict(task_id=self.task.id))],self.w.seconds)

    def test_pending_egg_is_preserved_and_only_original_unissued_quantity_is_stopped(self):
        self.assertEqual(len(self.q.tick(self.w,set())),1)
        egg=replace(self.w,game_loop=250,units=tuple(replace(u,type_id=103,orders=(1343,)) if u.tag==2 else u for u in self.w.units))
        self.q.tick(egg,set());pending=self.task.program.pending[0]
        self.stop();self.stop()
        self.assertIs(self.task.program.pending[0],pending)
        self.assertTrue(self.task.active)
        self.assertEqual(self.q.production_quantity(self.task),dict(requested=20,issued=2,observed_completed=0,
            unissued=0,awaiting_completion=2,remaining=2,cancelled_unissued=18))
        self.assertEqual(self.q.tick(replace(egg,game_loop=300),set()),[])
        born=replace(egg,game_loop=600,units=tuple(u for u in egg.units if u.tag!=2)+
            (self.f.unit(10,105),self.f.unit(11,105)))
        self.assertEqual(self.q.tick(born,set()),[])
        self.assertEqual(self.task.status,'completed');self.assertEqual(len(self.task.program.born),2)
        self.assertEqual(self.task.program.issued,2)

    def test_atomic_invalid_reply_does_not_stop_a_goal(self):
        self.q.tick(self.w,set())
        with self.assertRaises(PlanValidationError):self.q.enqueue([
            dict(skill='stop_production',args=dict(task_id=self.task.id)),dict(skill='imaginary')],11)
        self.assertEqual(self.task.count,20);self.assertEqual(getattr(self.task.program,'cancelled_unissued',0),0)

    def test_fully_unissued_stop_never_sends_native_orders(self):
        self.stop();self.assertEqual(self.task.status,'cancelled')
        self.assertEqual(self.q.production_quantity(self.task)['requested'],20)
        self.assertEqual(self.q.production_quantity(self.task)['cancelled_unissued'],20)
        self.assertEqual(self.q.tick(self.w,set()),[])

    def test_explicit_append_restarts_only_new_quantity_and_keeps_stopped_history(self):
        self.q.tick(self.w,set());self.stop()
        self.q.enqueue([dict(skill='train_unit',count=8,args=dict(unit='zergling',queue_mode='append'))],11,request_id=7)
        self.assertEqual(self.task.count,10)
        quantity=self.q.production_quantity(self.task)
        self.assertEqual(quantity['requested'],28);self.assertEqual(quantity['cancelled_unissued'],18)
        self.assertEqual(quantity['unissued'],8)
        self.q.enqueue([dict(skill='train_unit',count=8,args=dict(unit='zergling',queue_mode='append'))],12,request_id=7)
        self.assertEqual(self.task.count,10)

    def test_same_reply_stop_and_append_use_remaining_capacity_after_stop(self):
        self.q.tick(self.w,set())
        self.q.enqueue([dict(skill='stop_production',args=dict(task_id=self.task.id)),
            dict(skill='train_unit',count=198,args=dict(unit='zergling',queue_mode='append'))],11)
        self.assertEqual(self.q.production_quantity(self.task)['remaining'],200)
        self.assertEqual(self.q.production_quantity(self.task)['requested'],218)

    def test_coalescing_does_not_restart_stopped_quantity_and_another_stop_keeps_append_history(self):
        self.q.tick(self.w,set());self.stop()
        self.q.enqueue([dict(skill='train_unit',count=20,args=dict(unit='zergling'))],11)
        self.assertEqual(self.task.count,2)
        self.q.enqueue([dict(skill='train_unit',count=8,args=dict(unit='zergling',queue_mode='append'))],12)
        self.stop();quantity=self.q.production_quantity(self.task)
        self.assertEqual(quantity['requested'],28);self.assertEqual(quantity['cancelled_unissued'],26)
        self.assertEqual(quantity['unissued'],0);self.assertEqual(quantity['issued'],2)

    def test_same_reply_stop_before_first_purchase_and_append_creates_only_the_new_goal(self):
        self.q.enqueue([dict(skill='stop_production',args=dict(task_id=self.task.id)),
            dict(skill='train_unit',count=8,args=dict(unit='zergling',queue_mode='append'))],11)
        self.assertEqual(self.task.status,'cancelled')
        active=[t for t in self.q.tasks if t.active]
        self.assertEqual(len(active),1);self.assertEqual(active[0].count,8)
        self.assertEqual(self.q.production_quantity(self.task)['requested'],20)

    def test_late_stop_of_an_already_completed_known_goal_is_a_noop(self):
        before=capture(self.w,self.q)
        self.q.update(self.task,'completed','finished before the reply')
        plan=dict(tasks=[dict(skill='stop_production',args=dict(task_id=self.task.id))])
        self.assertEqual(revalidate(plan,before,self.w,self.q,(30,30),35),[])
        self.stop();self.assertEqual(self.task.status,'completed');self.assertEqual(self.task.count,20)

    def test_new_or_unknown_late_target_and_nonproduction_goals_are_rejected(self):
        before=capture(self.w,self.q)
        plan=dict(tasks=[dict(skill='stop_production',args=dict(task_id=self.task.id))])
        self.assertEqual(revalidate(plan,before,self.w,self.q,(30,30),35),[])
        self.q.enqueue([dict(skill='build_structure',args=dict(structure='extractor'))],11)
        for task_id in (self.q.tasks[-1].id,999):
            with self.assertRaises(PlanValidationError):self.q.enqueue([dict(skill='stop_production',args=dict(task_id=task_id))],12)
        self.assertTrue(revalidate(dict(tasks=[dict(skill='stop_production',args=dict(task_id=999))]),before,self.w,self.q,(30,30),35))

    def test_tool_is_in_the_real_race_schema(self):
        jsonschema.validate(dict(tasks=[dict(skill='stop_production',count=1,args=dict(task_id=1))],reason='只停后续购买'),
            output_schema(dict(tech_tree=self.f.tree.public('zerg'))))

    def test_ammo_is_excluded_even_when_the_native_catalogue_has_no_ammo_marker(self):
        from arena.native_queue_cancel import supported
        for race,name in (('terran','nuke'),('protoss','interceptor')):
            with self.subTest(name=name):
                q=TaskQueue(self.f.backend(race),0,lambda _:None)
                q.enqueue([dict(skill='train_unit',count=1,args=dict(unit=name))],10)
                target=q.tasks[0]
                self.assertFalse(q.stoppable_production(target))
                self.assertFalse(supported(target,self.f.tree))
                with self.assertRaises(PlanValidationError):
                    q.enqueue([dict(skill='stop_production',args=dict(task_id=target.id))],11)


if __name__=='__main__':unittest.main()
