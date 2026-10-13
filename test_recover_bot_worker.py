import json
from pathlib import Path
import tempfile
import unittest
from tools.recover_bot_worker import recover


class RecoverWorker(unittest.TestCase):
    def fixture(self,root):
        match=root/'match-02-hard';match.mkdir()
        budget=dict(limit=52,attempted=7,by_role={'battle':6,'review':1})
        state=dict(model='opencode-go/deepseek-v4.1-flash',budget=budget,
                   matches=[{'number':1,'report':str(root/'match-01-hard/match.json')}])
        report=dict(number=2,model=state['model'],difficulty='hard',cleanup_complete=True,
                    model_attempts=5,model_calls=4,outcome='Defeat',termination='engine_end',result_source='engine_player_result')
        for path,value in [(root/'campaign.json',state),(root/'worker-02.json',{'folder':str(match),'budget':budget}),
                           (match/'match.json',report),(match/'budget.json',dict(limit=52,attempted=12,by_role={'battle':11,'review':1}))]:
            path.write_text(json.dumps(value))
        return match,state

    def test_completed_worker_keeps_failed_calls_prior_history_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);match,old=self.fixture(root)
            state=recover(root,2)
            self.assertEqual(state['budget']['attempted'],12)
            self.assertEqual(state['matches'][0],old['matches'][0])
            self.assertEqual(state['matches'][1]['model_calls'],4)
            self.assertEqual(recover(root,2),state)

    def test_stale_or_inconsistent_budget_never_overwrites_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);match,old=self.fixture(root);before=(root/'campaign.json').read_bytes()
            (match/'budget.json').write_text(json.dumps(dict(limit=52,attempted=13,by_role={'battle':12,'review':1})))
            with self.assertRaisesRegex(ValueError,'accounting'):recover(root,2)
            self.assertEqual((root/'campaign.json').read_bytes(),before)


if __name__=='__main__':unittest.main()
