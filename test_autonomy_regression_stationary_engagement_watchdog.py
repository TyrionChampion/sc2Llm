"""A real in-range attack must count as progress for a spatial mission."""
from dataclasses import replace
import unittest

from arena.raw_engine import MOVE, ATTACK
from arena.tasks import TaskQueue
from test_rally_orders import engine_backend
from test_realtime_runtime import unit, world


class StationaryEngagementWatchdog(unittest.TestCase):
    def _mission(self, combat):
        _, backend = engine_backend('terran', available=(MOVE, ATTACK, 3673, 390))
        q = TaskQueue(backend, 0, lambda s: None, lambda *a, **kw: None)
        marine = unit(1, 48, 30, 30, ground_dps=10, attack_range=5,
                      engaged_target=2 if combat else 0)
        enemy = unit(2, 45, 34, 30, alliance=4, health=45, is_building=False)
        initial = world([marine, enemy] if combat else [marine], 10)
        q.enqueue([{'skill': 'order_units', 'args': {
            'unit': 'marine', 'count': 1, 'mode': 'attack_move',
            'target': [80, 80], 'timeout_s': 120, 'reason': 'test'}}], 10)
        q.tick(initial, set())
        q.tick(replace(initial, game_loop=int(11 * 22.4)), set())
        later_units = (marine, replace(enemy, health=20)) if combat else (marine,)
        q.tick(replace(initial, game_loop=int(32 * 22.4), units=later_units), set())
        return q

    def test_visible_in_range_engagement_with_damage_resets_stall_watchdog(self):
        q = self._mission(True)
        self.assertEqual(q.tasks[0].status, 'running')

    def test_noncombat_stationary_squad_still_times_out(self):
        q = self._mission(False)
        self.assertEqual(q.tasks[0].status, 'failed')
        self.assertIn('no observed movement progress', q.tasks[0].reason)


if __name__ == '__main__':
    unittest.main()
