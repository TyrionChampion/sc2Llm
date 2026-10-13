"""Adversarial scoring tests: prose, missing vision and unsafe routes cannot pass."""
import unittest
from dataclasses import replace
import numpy as np
from arena.tactical_scenarios import evaluate, safe_rally, record_scan, SCENARIOS
from test_rally_orders import grid
from test_realtime_runtime import unit, world


class TacticalScenarioTests(unittest.TestCase):
    def test_third_to_main_crosses_natural_pressure(self):
        g = grid()
        threats = [dict(pos=[58, 40], radius=8)]
        self.assertFalse(safe_rally(g, (84,40), (24,40), threats, max_distance=100))
        self.assertTrue(safe_rally(g, (84,40), (87,44), threats))
        self.assertTrue(safe_rally(g, (24,40), (28,43), threats))
        self.assertTrue(safe_rally(g, (44,40), (39,43), threats))

    def test_safe_destination_with_dangerous_route_is_rejected(self):
        self.assertFalse(safe_rally(grid(), (44,40), (64,40),
                                    [dict(pos=[54,40], radius=4)]))

    def test_textual_intent_or_scan_request_alone_cannot_pass(self):
        for name in SCENARIOS:
            r = evaluate(name, {'valid': True}, dict(model_calls=1,
                engine_action_errors=0, reason='已设置安全集结，已开雷达反击',
                model_requested_relevant_scan=True, model_changed_third_rally=True))
            self.assertEqual(r['verdict'], 'fail')

    def test_invalid_fixture_is_never_model_failure_or_pass(self):
        self.assertEqual(evaluate(SCENARIOS[1], {'high_ground_hidden': False}, {})['verdict'],
                         'inconclusive')
        self.assertEqual(evaluate(SCENARIOS[0], {}, {})['verdict'], 'inconclusive')

    def test_every_scan_link_is_required(self):
        good = dict.fromkeys(['model_requested_relevant_scan','scan_effect_observed',
            'orbital_energy_spent','high_ground_became_visible','own_counterfire_observed',
            'enemy_damage_after_scan'], True)
        good.update(model_calls=1, engine_action_errors=0)
        self.assertEqual(evaluate(SCENARIOS[1], {'valid': True}, good)['verdict'], 'pass')
        for key in list(good):
            bad = dict(good);bad[key] = 1 if key == 'engine_action_errors' else False
            self.assertNotEqual(evaluate(SCENARIOS[1], {'valid': True}, bad)['verdict'], 'pass', key)

    def test_all_bases_and_reinforcement_trace_are_required(self):
        good = dict.fromkeys(['model_changed_third_rally','all_three_rallies_safe',
            'rallies_held_for_window','third_produced_units','third_units_arrived',
            'third_units_survived','third_units_avoided_fire_zone'], True)
        good.update(model_calls=1, engine_action_errors=0)
        self.assertEqual(evaluate(SCENARIOS[0], {'valid': True}, good)['verdict'], 'pass')
        for key in list(good):
            bad = dict(good);bad[key] = 1 if key == 'engine_action_errors' else False
            self.assertNotEqual(evaluate(SCENARIOS[0], {'valid': True}, bad)['verdict'], 'pass', key)

    def test_one_shot_kill_does_not_require_a_surviving_visible_target(self):
        caster=unit(1,132,20,20,is_building=True,energy=50)
        tank=unit(2,32,50,50,ground_dps=35,attack_range=13)
        w=replace(world([caster,tank],10),effects=((6,1,58,50,13),),
                  visibility=np.full((200,200),2,dtype='uint8'),dead_units=(99,))
        e={'model_requested_relevant_scan':True}
        record_scan(e,w,world([],10),high=(58,50),orbital_tag=1,energy_before=100,
                    own_tags={2},enemy_health={99:45},previous_cooldowns={2:0},cooldowns={2:30})
        self.assertTrue(e['high_ground_became_visible'])
        self.assertTrue(e['own_counterfire_observed'])
        self.assertTrue(e['enemy_damage_after_scan'])

    def test_wrong_scan_or_energy_loss_without_effect_is_not_confirmation(self):
        w=replace(world([unit(1,132,20,20,energy=0)],10),effects=())
        e={'model_requested_relevant_scan':True}
        record_scan(e,w,world([],10),high=(58,50),orbital_tag=1,energy_before=100,
                    own_tags={2},enemy_health={99:45},previous_cooldowns={},cooldowns={})
        self.assertNotIn('orbital_energy_spent',e)


if __name__ == '__main__':
    unittest.main()
