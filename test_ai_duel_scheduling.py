"""Scheduling regression against timestamps extracted from the real native duel."""
import json
import unittest
from arena.commander import Commander
from verify_ai_duel_scheduling import EVIDENCE, replay


class DuelSchedulingTests(unittest.TestCase):
    def setUp(self):
        self.evidence=json.loads(EVIDENCE.read_text())['players']

    def test_recorded_combat_churn_cannot_request_each_game_second(self):
        for side,data in self.evidence.items():
            with self.subTest(side=side):
                requests=replay(data['events'],data['requests'][-1])
                combat=[s for s in requests if s>=480]
                urgent=[e['game_s'] for e in data['events'] if e['kind']=='tactical_base_alert']
                self.assertGreater(len(combat),10)  # Never starves despite continuous intel.
                self.assertLess(len(combat),len([s for s in data['requests'] if s>=480])/3)
                for a,b in zip(combat,combat[1:]):
                    if not any(a<=t<=b for t in urgent):self.assertGreaterEqual(b-a,10)
                for t in urgent:
                    following=next((s for s in requests if s>=t),None)
                    if following is not None:self.assertLessEqual(following-t,2+8/22.4)

    def test_recorded_rejection_then_intel_cannot_postpone_pending_retry(self):
        for data in self.evidence.values():
            rejection=next(e for e in data['events'] if e['kind']=='plan_rejected' and e['game_s']>480)
            following=next(e for e in data['events'] if e['kind']=='intelligence' and e['game_s']>rejection['game_s'])
            c=Commander.__new__(Commander);c.interval=20
            c.last_request=rejection['game_s']-1;c.next_request_game_s=float('inf')
            c._wake_request(rejection['game_s'],delay=3)
            due=c.next_request_game_s
            c._wake_request(following['game_s'])
            self.assertEqual(c.last_request,rejection['game_s']-1)
            self.assertLessEqual(c.next_request_game_s,due)
            self.assertTrue(c._request_due(due))


if __name__=='__main__':unittest.main()
