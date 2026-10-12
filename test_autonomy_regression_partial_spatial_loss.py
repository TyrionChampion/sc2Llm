"""Regression coverage for spatial missions after confirmed squad losses."""
import unittest
from dataclasses import replace

import numpy as np

from arena.raw_engine import ATTACK
from arena.tasks import TaskQueue
from test_rally_orders import engine_backend
from test_realtime_runtime import unit, world


class PartialSpatialLossRegression(unittest.TestCase):
    def setUp(self):
        self.fixture, self.backend = engine_backend('terran', available=(16, ATTACK, 3673, 390))
        self.events = []
        self.q = TaskQueue(self.backend, 0, lambda s: None,
                           lambda kind, **kw: self.events.append(dict(kind=kind, **kw)))
        self.m1, self.m2 = unit(1, 48, 30, 30), unit(2, 48, 31, 30)
        self.w = replace(world([self.m1, self.m2], 10), visibility=np.zeros((200, 200), dtype='uint8'))
        self.order = dict(skill='order_units', args=dict(
            unit='marine', count=2, mode='attack_move', target=[80, 80],
            reason='明确派遣两名陆战队'))

    def test_confirmed_dead_member_does_not_stop_survivor(self):
        self.q.enqueue([self.order], self.w.seconds)
        self.assertEqual(len(self.q.tick(self.w, set())), 1)
        task = self.q.tasks[0]

        later = replace(self.w, game_loop=246, units=(self.m2,), dead_units=(self.m1.tag,))
        actions = self.q.tick(later, set())

        self.assertEqual(actions, [])  # Existing attack order remains on the survivor.
        self.assertEqual(task.status, 'running')
        self.assertEqual(task.program.tags, {self.m2.tag})
        self.assertFalse(any(a.action_raw.unit_command.ability_id == 4 for a in actions))
        self.assertFalse(any(e['kind'] == 'spatial_mission_error' for e in self.events))

    def test_unobserved_member_is_not_assumed_dead_or_replaced(self):
        self.q.enqueue([self.order], self.w.seconds)
        self.q.tick(self.w, set())
        task = self.q.tasks[0]

        later = replace(self.w, game_loop=246, units=(self.m2,))
        self.assertEqual(self.q.tick(later, set()), [])

        self.assertEqual(task.status, 'waiting')
        self.assertEqual(task.program.tags, {self.m1.tag, self.m2.tag})
        self.assertEqual(task.program.allocated_count, 2)


if __name__ == '__main__':
    unittest.main()
