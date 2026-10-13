import unittest
from arena.autonomous_bot import BotPlanner, RequestBudget, match_time_policy
from test_autonomous_bot import Client


class WallDeadline(unittest.TestCase):
    def test_slower_exposes_real_wall_deadline_without_changing_caps(self):
        policy=match_time_policy(300,700,1800,1860,'slower')
        self.assertEqual(policy['max_game_s'],1800)
        self.assertEqual(policy['max_wall_s'],1860)
        self.assertEqual(policy['remaining_wall_s'],1160)
        self.assertAlmostEqual(policy['estimated_game_deadline_s'],797.1,places=1)
        expired=match_time_policy(780,1865,1800,1860,'slower')
        self.assertEqual(expired['remaining_wall_s'],0)
        self.assertEqual(expired['estimated_game_deadline_s'],780)

    def test_adaptive_cadence_uses_shorter_deadline_and_keeps_request_ceiling(self):
        planner=BotPlanner(Client(),RequestBudget(26),'自主制定战术',adaptive_cadence=True)
        planner.client.attempts=18
        planner.policy=match_time_policy(300,700,1800,1860,'slower')
        self.assertAlmostEqual(planner.decision_interval(),82.85,places=2)
        planner.client.attempts=24
        self.assertFalse(planner.ready_for_request())


if __name__=='__main__':unittest.main()
