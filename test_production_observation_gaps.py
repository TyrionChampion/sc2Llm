"""Regression from non-realtime duels: birth travel and returning old workers."""
from dataclasses import replace
import unittest

from arena.tasks import TaskQueue
from test_rally_orders import engine_backend
from test_realtime_runtime import world, unit


class ProductionObservationGaps(unittest.TestCase):
    def setUp(self):
        self.fixture,self.b=engine_backend('protoss',available=(917,1006))
        self.q=TaskQueue(self.b,0,lambda _:None)
        self.gate=unit(1,62,is_building=True,powered=True,radius=1.5)
        self.core=unit(2,72,34,30,is_building=True)
        self.w=replace(world([self.gate,self.core],10),vespene=500,race='protoss')

    def start_stalker(self):
        self.q.enqueue([dict(skill='train_unit',args={'unit':'stalker'})],self.w.seconds)
        self.q.tick(self.w,set())
        self.q.tick(replace(self.w,game_loop=self.w.game_loop+1,
            units=(replace(self.gate,orders=(917,)),self.core)),set())

    def test_regular_gateway_birth_can_move_during_64_frame_observation_gap(self):
        self.start_stalker()
        born=unit(3,74,38.5,30,ground_dps=10)
        self.q.tick(replace(self.w,game_loop=self.w.game_loop+65,units=(self.gate,self.core,born)),set())
        self.assertEqual(self.q.tasks[0].status,'completed')
        self.assertEqual(self.q.tasks[0].program.born,{3})

    def test_an_unreachable_distant_unit_is_not_claimed_as_this_birth(self):
        self.start_stalker()
        born=unit(3,74,140,140,ground_dps=10)
        self.q.tick(replace(self.w,game_loop=self.w.game_loop+65,units=(self.gate,self.core,born)),set())
        self.assertEqual(self.q.tasks[0].program.born,set())
        self.assertTrue(self.q.tasks[0].active)

    def test_same_frozen_frame_does_not_grant_a_large_movement_allowance(self):
        self.start_stalker()
        born=unit(3,74,38.5,30,ground_dps=10)
        self.q.tick(replace(self.w,game_loop=self.w.game_loop+1,units=(self.gate,self.core,born)),set())
        self.assertEqual(self.q.tasks[0].program.born,set())

    def test_old_worker_returning_from_gas_is_not_a_newly_trained_worker(self):
        nexus=unit(1,59,is_building=True)
        old_worker=unit(2,84);other_worker=unit(3,84)
        initial=replace(self.w,units=(nexus,old_worker,other_worker))
        self.q.tick(initial,set())
        absent=replace(initial,game_loop=initial.game_loop+22,units=(nexus,other_worker))
        self.q.enqueue([dict(skill='train_worker')],absent.seconds)
        self.q.tick(absent,set())
        self.q.tick(replace(absent,game_loop=absent.game_loop+1,
            units=(replace(nexus,orders=(1006,)),old_worker,other_worker)),set())
        self.assertEqual(self.q.tasks[0].program.born,set())
        self.assertTrue(self.q.tasks[0].active)
        new_worker=unit(4,84)
        self.q.tick(replace(absent,game_loop=absent.game_loop+300,
            units=(nexus,old_worker,other_worker,new_worker)),set())
        self.assertEqual(self.q.tasks[0].program.born,{4})
        self.assertEqual(self.q.tasks[0].status,'completed')


if __name__=='__main__':unittest.main()
