"""Repeated executor descriptions must not crowd current observations out of a request."""
import json
import unittest
from arena.autonomous_bot import BotPlanner,RequestBudget
from arena.context_tables import decode_tables
from test_autonomous_bot import Client


class ContextProse(unittest.TestCase):
    def test_report_contracts_can_be_omitted_while_current_flags_and_evidence_remain(self):
        control=dict(automatic_script_defense=False,python_tactics_enabled=False,
            defense_orders=dict(enabled=True,scope='Model-ordered persistent base defence; no automatic defence, no model call per tick',
                order='defend_base args={reason,timeout_s?}; cancel with cancel_task args={task_id} before switching to offence',
                holds='free combat units only; custom skills, other missions and workers are never taken'),
            combat_micro=dict(enabled=True,abilities=['stim','siege','unsiege'],
                scope='After explicit attack_nearest/move_out/order_units or defensive assignment only; scouts and active Python/JSON/morph units excluded'))
        payload=dict(world=dict(game_s=639.3,current_units='x'*38000),control_policy=control,
            prepared_strategy='current model-selected goals '*60,
            combat_micro=dict(enabled=True,managed_units=6,counts={'confirmed':18},
                scope='Explicit move_out/attack_nearest/order_units and defense units only; scouts and active Python/JSON/morph owners excluded'),
            defensive_rally=dict(state='active',pos=[40,30],
                scope='barracks/factory/starport, gateway/robotics/stargate, hatchery/lair/hive unit rallies; no worker rally, warp-in or mobile-unit override'),
            feedback=dict(events=[dict(id=458,error='Actual rejected plan')]))
        planner=BotPlanner(Client(),RequestBudget(26),'current')
        planner.policy=dict(game_s=639.3,max_game_s=1800,max_wall_s=1860)
        planner.chat('',json.dumps(payload))
        value=decode_tables(planner.client.payload)
        self.assertEqual(value['world'],payload['world'])
        self.assertEqual(value['feedback'],payload['feedback'])
        self.assertFalse(value['control_policy']['automatic_script_defense'])
        self.assertTrue(value['control_policy']['combat_micro']['enabled'])
        self.assertEqual(value['combat_micro']['counts'],{'confirmed':18})
        self.assertEqual(value['defensive_rally']['pos'],[40,30])
        self.assertIn('control_policy.defense_orders.order',value['context_policy']['omitted_report_prose'])
        self.assertIn('order',control['defense_orders'])
        self.assertEqual(planner.budget.used,1)


if __name__=='__main__':unittest.main()
