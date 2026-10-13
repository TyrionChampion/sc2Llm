import json
import unittest
from arena.autonomous_bot import BotPlanner,RequestBudget
from arena.battle_plans import bounded_note
from test_autonomous_bot import Client


class CompleteStrategy(unittest.TestCase):
    def test_review_tail_reaches_battle_before_the_unchanged_context_guard(self):
        strategy='已选战术说明'*210+'\n末段：480秒前不开二矿；侦察重科技则提前450秒出击。'
        self.assertGreater(len(strategy),1200)
        self.assertLessEqual(len(strategy),1800)
        planner=BotPlanner(Client(),RequestBudget(26),strategy)
        payload=dict(world={'game_s':0},prepared_strategy=bounded_note(strategy,1200))
        self.assertTrue(payload['prepared_strategy']['truncated'])
        planner.chat('',json.dumps(payload))
        note=planner.client.payload['prepared_strategy']
        self.assertEqual(note['text'],strategy)
        self.assertFalse(note['truncated'])
        self.assertEqual(note['original_chars'],len(strategy))
        self.assertEqual(planner.budget.used,1)


if __name__=='__main__':unittest.main()
