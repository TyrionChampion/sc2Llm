"""Scenario prerequisites: player-local rallies, damage and static high ground."""
from dataclasses import replace
import unittest
import numpy as np

from arena.rally import producer_inventory
from arena.spatial import Battlefield
from test_rally_orders import engine_backend
from test_realtime_runtime import unit, world


class TacticalObservationTests(unittest.TestCase):
    def setUp(self):
        _, self.backend = engine_backend('terran')
        self.field = Battlefield(self.backend, 0)
        self.marine = unit(10, 48, 50, 50, health=45,
                           ground_dps=6, attack_range=5)
        self.w = replace(world([self.marine], 10),
                         visibility=np.ones((200, 200), dtype='uint8'))
        self.backend.grid.height[:] = 128
        self.backend.grid.height[42:60, 56:68] = 144

    def test_each_hatchery_exposes_its_observed_rallies(self):
        bases = [unit(i, 86, x, 40, is_building=True,
                      rally_targets=((target, 40., 0), (x, 44., 900+i)))
                 for i, x, target in [(1, 24, 28), (2, 44, 39), (3, 84, 24)]]
        rows, _ = producer_inventory(self.backend, world(bases, 1), 0)
        self.assertEqual(rows[2]['rally_targets'], [[24, 40., 0], [84, 44., 903]])
        self.assertEqual(rows[0]['rally_targets'][0][:2], [28, 40])

    def test_damage_and_nearby_fogged_high_ground_are_available(self):
        self.field.update(self.w)
        damaged = replace(self.w, game_loop=self.w.game_loop+22,
                          units=(replace(self.marine, health=30),))
        facts = self.field.public(damaged)['terrain_context']
        self.assertEqual(facts['recent_own_damage'][0]['health_lost'], 15)
        self.assertEqual(facts['recent_own_damage'][0]['pos'], [50, 50])
        self.assertTrue(any(p['height'] > 128 and p['visibility'] != 2
                            for p in facts['nearby_higher_ground']))
        self.assertNotIn('enemy', str(facts['nearby_higher_ground']))

    def test_missing_height_does_not_invent_high_ground(self):
        self.backend.grid = None
        facts = self.field.public(self.w)['terrain_context']
        self.assertFalse(facts['available'])
        self.assertEqual(facts['nearby_higher_ground'], [])

    def test_damage_expires_and_repeated_snapshot_is_not_double_counted(self):
        self.field.update(self.w)
        damaged = replace(self.w, game_loop=self.w.game_loop+22,
                          units=(replace(self.marine, health=30),))
        self.field.public(damaged)
        facts = self.field.public(damaged)['terrain_context']
        self.assertEqual(len(facts['recent_own_damage']), 1)
        later = replace(damaged, game_loop=damaged.game_loop+22*40)
        self.assertEqual(self.field.public(later)['terrain_context']['recent_own_damage'], [])

    def test_point_rally_reports_fire_zone_crossing_without_choosing_new_target(self):
        bases=[unit(1,86,84,40,is_building=True,rally_targets=((24.,40.,0),))]
        enemy=unit(99,48,58,40,alliance=4,ground_dps=6,attack_range=5)
        rows,_=producer_inventory(self.backend,world(bases+[enemy],1),0)
        route=rows[0]['point_rally_routes'][0]
        self.assertTrue(route['observed_fire_zone_overlap'])
        self.assertEqual(route['target'],[24.,40.])
        self.assertNotIn('suggested_target',route)

    def test_local_safe_rally_is_distinct_from_unknown_enemy_occupancy(self):
        b=unit(1,86,84,40,is_building=True,rally_targets=((88.,44.,0),))
        route=producer_inventory(self.backend,world([b],1),0)[0][0]['point_rally_routes'][0]
        self.assertFalse(route['observed_fire_zone_overlap'])
        self.assertEqual(route['threat_scope'],'currently observed ground weapons only; fog not cleared')

    def test_known_enemy_height_and_terrain_visibility_are_correlated_with_own_damage(self):
        enemy=unit(99,48,60,50,alliance=4,ground_dps=6,attack_range=5)
        self.field.update(replace(self.w,units=(self.marine,enemy)))
        w=replace(self.w,game_loop=self.w.game_loop+22,
                  units=(replace(self.marine,health=30),enemy))
        contact=self.field.public(w)['terrain_context']['combat_contacts'][0]
        self.assertEqual(contact['enemy_pos'],[60,50])
        self.assertTrue(contact['higher_than_own'])
        self.assertEqual(contact['enemy_position_visibility'],1)
        self.assertTrue(contact['own_recently_damaged'])
        self.assertFalse(contact['own_targeting_contact'])

    def test_missing_enemy_contact_never_uses_oracle_to_locate_attacker(self):
        self.field.update(self.w)
        damaged=replace(self.w,game_loop=self.w.game_loop+22,units=(replace(self.marine,health=30),))
        facts=self.field.public(damaged)['terrain_context']
        self.assertEqual(facts['combat_contacts'],[])


if __name__ == '__main__':
    unittest.main()
