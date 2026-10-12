"""Repeated failures and verified prior wins must reach the strategic review."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from arena.autonomous_review import campaign_review_history,review_game
from arena.autonomous_bot import RequestBudget
from arena.context_tables import decode_tables


class ReviewHistory(unittest.TestCase):
    def test_large_review_losslessly_restores_all_samples_before_one_model_call(self):
        worlds=[dict(game_s=25*i,units=[dict(tag=j,unit='Marine',health=45,health_max=45,
                    type_id=48,pos=[j,i]) for j in range(50)]) for i in range(24)]
        original=dict(errors=[],timeline=[dict(world=w,active_tasks=[]) for w in worlds])
        class Client:
            payload=None
            def __init__(self,*args):pass
            def chat(self,system,user):
                Client.payload=json.loads(user)
                return json.dumps(dict(tasks=[],category='strategy',strategy='Choose according to observations'))
            def close(self):pass
        budget=RequestBudget(2)
        with patch('arena.autonomous_review.ChatGPTSubscriptionPlanner',Client):
            review_game('chatgpt/gpt-6-luna','/tmp/unused',budget,original,'initial','hard')
        restored=decode_tables(Client.payload)['evidence']
        self.assertEqual([restored['timeline_worlds'][row['world_index']]
                          for row in restored['timeline']],worlds)
        self.assertEqual(budget.by_role,{'review':1})
        self.assertEqual(original['timeline'][0]['world'],worlds[0])

    def test_recent_outcomes_preserve_unknown_and_reference_requires_native_win(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);folder=root/'current';folder.mkdir();old=root/'previous';old.mkdir()
            entries=[]
            for number,outcome in enumerate(('Defeat','Defeat','Unknown','Defeat'),1):
                match=folder/f'match-{number}';match.mkdir();report=match/'match.json'
                report.write_text(json.dumps(dict(outcome=outcome,result_source='unverified' if outcome=='Unknown' else 'engine_player_result',termination='context_budget' if outcome=='Unknown' else 'engine_end',timeline=[dict(world=dict(own={'Marine':number},minerals=100*number))])))
                entries.append(dict(number=number,report=str(report)))
            earlier=old/'campaign.json';earlier.write_text('{}');win=old/'match.json';replay=old/'match.SC2Replay';replay.write_bytes(b'controlled fixture')
            data=dict(outcome='Victory',difficulty='hard',result_source='engine_player_result',engine_results={'1':'Victory'},replay=str(replay),final_world={'game_s':532.2})
            win.write_text(json.dumps(data));(old/'winning-strategy.json').write_text(json.dumps(dict(strategy='Observed mixed-army tactic',report=str(win))))
            (folder/'campaign.json').write_text(json.dumps(dict(matches=entries,previous_campaign=dict(path=str(earlier)))))
            result=campaign_review_history(folder/'match-4/review')
            self.assertEqual([row['number'] for row in result['recent_matches']],[1,2,3])
            self.assertEqual(result['recent_matches'][-1]['outcome'],'Unknown')
            self.assertEqual(result['verified_historical_reference']['strategy'],'Observed mixed-army tactic')
            data['engine_results']={'1':'Defeat'};win.write_text(json.dumps(data))
            self.assertNotIn('verified_historical_reference',campaign_review_history(folder/'match-4/review'))


if __name__=='__main__':unittest.main()
