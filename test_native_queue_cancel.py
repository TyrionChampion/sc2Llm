"""Only cancel observed paid queues belonging to the selected macro goal."""
import unittest
from dataclasses import replace
from types import SimpleNamespace
from arena.tasks import TaskQueue,PlanValidationError
from arena.native_queue_cancel import report
from arena.battle_plans import output_schema
import test_multirace as fixtures


class QueueCancel(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.MultiRace();self.f.setUp();self.events=[]
        self.backend=self.f.backend('terran',(3671,))
        self.q=TaskQueue(self.backend,0,lambda _:None,lambda kind,**kw:self.events.append(dict(kind=kind,**kw)))
        self.q.enqueue([dict(skill='train_unit',count=3,args=dict(unit='marine'))],0)
        self.original=self.q.tasks[-1];run=self.original.program
        run.issued=2;run.baseline=set();run.tags={2}
        run.pending=[dict(tags=[2],ability=560,quantity=1,seen=True,sent=1,minerals=50,gas=0,supply=1) for _ in range(2)]
        self.actor=self.f.unit(2,21,is_building=True,orders=(560,560))
        self.w=self.f.world([self.actor])
    def cancel(self):
        self.q.enqueue([dict(skill='cancel_native_queue',args=dict(task_id=self.original.id))],self.w.seconds,request_id=7)
        return self.q.tasks[-1]
    def test_linked_queue_is_cancelled_one_slot_per_fresh_ack(self):
        task=self.cancel();self.assertEqual(self.original.status,'cancelled')
        first=self.q.tick(self.w,set());self.assertEqual([a.action_raw.unit_command.ability_id for a in first],[3671])
        self.assertEqual(self.q.tick(replace(self.w,game_loop=230),set()),[])
        second=self.q.tick(replace(self.w,game_loop=250,units=(replace(self.actor,orders=(560,)),)),set())
        self.assertEqual([a.action_raw.unit_command.ability_id for a in second],[3671])
        self.assertEqual(self.q.tick(replace(self.w,game_loop=270,units=(replace(self.actor,orders=()),)),set()),[])
        self.assertEqual(task.status,'completed');self.assertEqual(task.program.dispatches,2)
        self.assertNotIn(2,self.q.protected());self.assertEqual(self.original.program.issued,2)
        self.assertFalse(task.program.summary()['refund_confirmed'])
    def test_unrelated_order_and_queue_growth_are_never_cancelled(self):
        for orders in ((560,561),(560,560,560)):
            with self.subTest(orders=orders):
                self.setUp();task=self.cancel()
                self.assertEqual(self.q.tick(replace(self.w,units=(replace(self.actor,orders=orders),)),set()),[])
                self.assertEqual(task.status,'failed');self.assertEqual(task.program.dispatches,0)
    def test_another_task_owner_prevents_all_dispatches(self):
        task=self.cancel();other=SimpleNamespace(active=True,skill='use_ability',id=99,program=SimpleNamespace(tags={2}),worker=None)
        self.q.tasks.append(other)
        with self.assertRaises(ValueError):task.program.advance(self.w,self.q,task,set(),[])
        self.assertEqual(task.program.dispatches,0)
        self.q.tasks.remove(other)
    def test_nonexclusive_rally_does_not_own_the_queue(self):
        task=self.cancel();other=SimpleNamespace(active=True,skill='rally_units',id=99,program=SimpleNamespace(tags={2}),worker=None)
        self.q.tasks.append(other);out=[]
        task.program.advance(self.w,self.q,task,set(),out)
        self.assertEqual(len(out),1);self.q.tasks.remove(other)
    def test_rejection_and_deadline_stop_without_recast(self):
        task=self.cancel();self.q.tick(self.w,set())
        self.assertEqual(self.q.tick(replace(self.w,game_loop=240,errors=((2,3671,3),)),set()),[])
        self.assertEqual(task.status,'failed');self.assertEqual(task.program.dispatches,1)
        self.setUp();task=self.cancel();self.q.tick(self.w,set())
        self.assertEqual(self.q.tick(replace(self.w,game_loop=950),set()),[])
        self.assertEqual(task.status,'failed');self.assertEqual(task.program.dispatches,1)
    def test_unissued_completed_and_repeated_goals_are_noops(self):
        for kind in ('unissued','completed','repeated'):
            with self.subTest(kind=kind):
                self.setUp()
                if kind=='unissued':self.original.program.issued=0;self.original.program.pending=[]
                if kind=='completed':self.q.update(self.original,'completed','observed')
                if kind=='repeated':self.original.native_queue_cancellation_completed=True
                task=self.cancel();self.assertEqual(self.q.tick(self.w,set()),[])
                self.assertEqual(task.status,'completed');self.assertEqual(task.program.dispatches,0)
    def test_illegal_plan_is_atomic_and_buildings_or_eggs_are_not_accepted(self):
        self.q.enqueue([dict(skill='build_supply')],0);building=self.q.tasks[-1]
        for target in (999,building.id):
            with self.assertRaises(PlanValidationError):self.q.enqueue([
                dict(skill='cancel_native_queue',args=dict(task_id=target)),dict(skill='train_worker')],10)
            self.assertTrue(self.original.active)
        with self.assertRaises(PlanValidationError):self.q.enqueue([
            dict(skill='cancel_native_queue',args=dict(task_id=self.original.id)),dict(skill='not_a_tool')],10)
        self.assertTrue(self.original.active)
    def test_report_schema_gate_and_research_queue(self):
        self.assertEqual(report(self.q)['supported_tasks'][0]['task_id'],self.original.id)
        plain=output_schema({});enabled=output_schema(dict(native_queue_cancellation=report(self.q)))
        import json
        self.assertNotIn('cancel_native_queue',json.dumps(plain));self.assertIn('cancel_native_queue',json.dumps(enabled))
        self.q=TaskQueue(self.backend,0,lambda _:None)
        self.q.enqueue([dict(skill='research',args=dict(upgrade='stimpack'))],0)
        self.original=self.q.tasks[-1];run=self.original.program;run.issued=1;run.tags={2}
        ability=run.spec['entry']['recipes'][0]['ability']
        run.pending=[dict(tags=[2],ability=ability,quantity=1,seen=True,sent=1)]
        self.w=self.f.world([self.f.unit(2,37,is_building=True,orders=(ability,))])
        task=self.cancel();self.assertEqual(len(self.q.tick(self.w,set())),1)
        self.q.tick(replace(self.w,game_loop=250,units=(replace(self.w.units[0],orders=()),)),set())
        self.assertEqual(task.status,'completed')


if __name__=='__main__':unittest.main()
