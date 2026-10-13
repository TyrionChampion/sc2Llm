"""Native redirected larva orders must confirm eggs without fabricating completion."""
import unittest
from dataclasses import replace
from arena.tasks import TaskQueue
from arena.macro import MacroRun,specification
import test_multirace as fixtures

class LarvaConfirmation(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.MultiRace();self.f.setUp();self.q=TaskQueue(self.f.backend('zerg'),0,lambda _:None)
        self.q.enqueue([dict(skill='train_worker',count=1,args={})],0)
        self.t=self.q.tasks[0];self.run=MacroRun(specification(self.f.tree,'zerg','train_worker',{}),{})
        self.t.program=self.run;self.run.baseline=set();self.run.issued=1
        self.run.pending=[dict(tags=[1],ability=1342,sent=0,seen=False,consumes_actor=True,
            larva_purchase=True,producer_pos=(30,30),eggs_before=[10])]
    def tick(self,units):
        w=replace(self.f.world(units,race='zerg'),game_loop=224)
        self.run.tick(w,self.q,self.t,set(),500,[])
    def test_redirected_egg_remains_running_beyond_eight_seconds_until_drone_exists(self):
        self.tick([self.f.unit(1,151),self.f.unit(2,103,orders=(1342,))])
        self.assertTrue(self.t.active);self.assertEqual(self.run.pending[0]['tags'],[2])
        self.assertTrue(self.run.pending[0]['seen']);self.assertEqual(self.run.born,set())
        self.tick([self.f.unit(3,104)])
        self.assertEqual(self.t.status,'completed');self.assertEqual(self.run.born,{3})
    def test_unrelated_old_and_enemy_eggs_do_not_acknowledge_a_purchase(self):
        for egg in (self.f.unit(2,103,orders=(1344,)),self.f.unit(10,103,orders=(1342,)),
                    replace(self.f.unit(2,103,orders=(1342,)),alliance=4)):
            with self.subTest(egg=egg):
                self.setUp();self.tick([self.f.unit(1,151),egg])
                self.assertEqual(self.t.status,'failed');self.assertFalse(self.run.pending[0]['seen'])
    def test_native_purchase_can_use_an_egg_at_another_owned_base(self):
        self.tick([self.f.unit(1,151),replace(self.f.unit(2,103,orders=(1342,)),x=80)])
        self.assertTrue(self.t.active);self.assertTrue(self.run.pending[0]['seen'])
        self.assertEqual(self.run.pending[0]['tags'],[2]);self.assertEqual(self.run.born,set())
    def test_one_egg_cannot_acknowledge_two_pending_purchases(self):
        self.t.count=2;self.run.issued=2
        self.run.pending.append(dict(self.run.pending[0],tags=[3]))
        self.tick([self.f.unit(1,151),self.f.unit(3,151),self.f.unit(2,103,orders=(1342,))])
        self.assertEqual(self.t.status,'failed')
        self.assertEqual(sum(bool(p['seen']) for p in self.run.pending),1)
    def test_two_native_orders_swapping_requested_larvae_do_not_deadlock_egg_claims(self):
        self.q.enqueue([dict(skill='build_supply',count=1,args={})],0)
        other=self.q.tasks[-1];other_run=other.program
        other_run.baseline=set();other_run.issued=1
        other_run.pending=[dict(tags=[2],ability=1344,sent=0,seen=False,consumes_actor=True,
            larva_purchase=True,producer_pos=(30,30),eggs_before=[])]
        w=replace(self.f.world([self.f.unit(1,103,orders=(1344,)),self.f.unit(2,103,orders=(1342,))],race='zerg'),game_loop=224)
        self.run.tick(w,self.q,self.t,set(),500,[]);other_run.tick(w,self.q,other,set(),500,[])
        self.assertTrue(self.t.active);self.assertTrue(other.active)
        self.assertEqual(self.run.pending[0]['tags'],[2]);self.assertEqual(other_run.pending[0]['tags'],[1])
        self.assertEqual(self.run.born,set());self.assertEqual(other_run.born,set())

if __name__=='__main__':unittest.main()
