"""SC2 weapon reach uses the radii of the attacker and the visible target."""
from dataclasses import replace
import unittest
from arena.raw_engine import MOVE, ATTACK
from arena.tasks import TaskQueue
from test_rally_orders import engine_backend
from test_realtime_runtime import unit, world


class LargeTargetEngagement(unittest.TestCase):
    def mission(self, distance=7.5, timeout=120):
        _,backend=engine_backend('terran',available=(MOVE,ATTACK,3673,390))
        queue=TaskQueue(backend,0,lambda s:None,lambda *a,**kw:None)
        marine=unit(1,48,31.5+distance,113.5,ground_dps=10,attack_range=5,
                    radius=.375,engaged_target=2)
        base=unit(2,18,31.5,113.5,alliance=4,is_building=True,health=1000,radius=2.5)
        initial=world([marine,base],10)
        queue.enqueue([{'skill':'order_units','args':{'unit':'marine','count':1,
            'mode':'attack_move','target':[80,80],'timeout_s':timeout,'reason':'攻击可见基地'}}],10)
        queue.tick(initial,set())
        queue.tick(replace(initial,game_loop=int(11*22.4)),set())
        later=replace(initial,game_loop=int(32*22.4),units=(marine,replace(base,health=700)))
        queue.tick(later,set())
        return queue,later

    def test_large_building_surface_in_range_counts_observed_damage(self):
        queue,_=self.mission()
        self.assertEqual(queue.tasks[0].status,'running')

    def test_distant_target_damage_does_not_renew_stalled_squad(self):
        queue,_=self.mission(distance=12)
        self.assertEqual(queue.tasks[0].status,'failed')
        self.assertIn('no observed movement progress',queue.tasks[0].reason)

    def test_valid_combat_never_extends_absolute_mission_deadline(self):
        queue,later=self.mission(timeout=40)
        queue.tick(replace(later,game_loop=int(52*22.4)),set())
        self.assertEqual(queue.tasks[0].status,'failed')
        self.assertIn('deadline',queue.tasks[0].reason)
