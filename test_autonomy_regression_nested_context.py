"""Large observed site lists must fit without dropping facts or adding model calls."""
import json
import unittest
from arena.autonomous_bot import BotPlanner,RequestBudget
from arena.context_tables import decode_tables
from test_autonomous_bot import Client


class NestedContext(unittest.TestCase):
    def test_large_expansion_observations_fit_and_roundtrip(self):
        sites=[dict(label='site_'+str(i),position=[i,30],remaining_minerals=i*701,
                    owned=False,visible=True,available=False,reason='observed mineral cluster')
               for i in range(35)]
        payload=dict(world=dict(game_s=612.6,current_units='x'*38000),expansion_sites=sites,
            feedback=dict(events=[dict(id=1,error='Actual current rejected order')]),
            intelligence=dict(contacts=[dict(unit='SiegeTank',count=5)]))
        planner=BotPlanner(Client(),RequestBudget(26),'current measured phase goal')
        planner.policy=dict(game_s=612.6,max_game_s=1800,max_wall_s=1860)
        planner.chat('',json.dumps(payload))
        restored=decode_tables(planner.client.payload)
        for key in payload:self.assertEqual(restored[key],payload[key])
        self.assertEqual(planner.budget.used,1)
        self.assertEqual(payload['expansion_sites'],sites)


if __name__=='__main__':unittest.main()
