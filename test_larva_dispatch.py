"""In-flight native larva purchases stay exclusive and finite products get turns."""
from dataclasses import replace
import unittest

from arena.tasks import TaskQueue
import test_multirace as fixtures


class LarvaDispatch(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.MultiRace();self.fixture.setUp()
        self.backend=self.fixture.backend('zerg',[1342,1343,1344,1351,1632])
        self.q=TaskQueue(self.backend,0,lambda _:None)
        self.w=self.fixture.world([self.fixture.unit(i,151) for i in (1,2,3)]+
            [self.fixture.unit(10,86,is_building=True),self.fixture.unit(11,89,is_building=True)],race='zerg')

    def tasks(self):
        self.q.enqueue([dict(skill='train_worker',count=2),
            dict(skill='train_unit',count=2,args=dict(unit='roach')),
            dict(skill='train_unit',count=4,args=dict(unit='zergling')),
            dict(skill='build_supply',count=1)],self.w.seconds)

    def tick(self,world=None):return self.q.tick(world or self.w,set())
    def acknowledge(self,tag,ability):
        self.w=replace(self.w,game_loop=self.w.game_loop+14,units=tuple(
            replace(u,type_id=103,orders=(ability,)) if u.tag==tag else u for u in self.w.units))
        return self.tick()

    def test_one_unconfirmed_larva_purchase_blocks_other_goals_and_same_goal(self):
        self.tasks();first=self.tick()
        self.assertEqual([a.action_raw.unit_command.ability_id for a in first],[1342])
        self.w=replace(self.w,game_loop=self.w.game_loop+14)
        self.assertEqual(self.tick(),[])
        self.assertEqual([t.program.issued for t in self.q.tasks],[1,0,0,0])
        self.assertEqual([t.count for t in self.q.tasks],[2,2,4,1])
        self.assertIn('acknowledgement',self.q.tasks[1].reason)

    def test_redirected_egg_acknowledgement_releases_turn_without_waiting_for_birth(self):
        self.tasks();self.tick()
        # Native larvae may be swapped: a different original tag is now the egg.
        self.assertEqual(self.acknowledge(3,1342),[])
        self.assertEqual(self.q.tasks[0].program.pending[0]['tags'],[3])
        self.assertTrue(self.q.tasks[0].program.pending[0]['seen'])
        self.w=replace(self.w,game_loop=self.w.game_loop+14)
        out=self.tick()
        self.assertEqual([a.action_raw.unit_command.ability_id for a in out],[1351])
        self.assertEqual(self.q.tasks[0].program.born,set())
        self.assertEqual(self.acknowledge(2,1351),[])
        self.w=replace(self.w,game_loop=self.w.game_loop+14)
        self.assertEqual([a.action_raw.unit_command.ability_id for a in self.tick()],[1343])
        self.assertEqual(self.acknowledge(1,1343),[])
        self.w=replace(self.w,game_loop=self.w.game_loop+14,
            units=self.w.units+(self.fixture.unit(4,151),))
        self.assertEqual([a.action_raw.unit_command.ability_id for a in self.tick()],[1344])
        self.assertEqual([t.program.issued for t in self.q.tasks],[1,1,2,1])

    def test_larva_confirmation_wait_does_not_block_queen_training(self):
        self.q.enqueue([dict(skill='train_worker',count=2),
            dict(skill='train_unit',count=1,args=dict(unit='queen'))],self.w.seconds)
        actions=self.tick()
        self.assertEqual([a.action_raw.unit_command.ability_id for a in actions],[1342,1632])

    def test_product_without_gas_does_not_block_the_next_requested_larva_product(self):
        self.tasks();self.tick();self.acknowledge(3,1342)
        self.w=replace(self.w,game_loop=self.w.game_loop+14,vespene=0)
        out=self.tick()
        self.assertEqual([a.action_raw.unit_command.ability_id for a in out],[1343])
        self.assertEqual(self.q.tasks[1].program.issued,0)
        self.assertEqual(self.q.tasks[2].program.issued,2)

    def test_missing_acknowledgement_fails_without_hidden_retry_or_replacement(self):
        self.q.enqueue([dict(skill='train_worker',count=2)],self.w.seconds)
        self.assertEqual(len(self.tick()),1)
        self.w=replace(self.w,game_loop=self.w.game_loop+int(9*22.4))
        self.assertEqual(self.tick(),[])
        self.assertEqual(self.q.tasks[0].status,'failed')
        self.assertEqual(self.q.tasks[0].program.issued,1)
        self.assertEqual(self.tick(),[]);self.assertEqual(len(self.q.tasks),1)


if __name__=='__main__':unittest.main()
