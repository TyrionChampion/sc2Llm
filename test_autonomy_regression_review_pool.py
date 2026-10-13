import json
import unittest
from unittest.mock import patch
from arena.autonomous_bot import RequestBudget
from arena.autonomous_review import compact_review_evidence, review_game
from arena.context_tables import object_pool, decode_tables


class ReviewPool(unittest.TestCase):
    def test_repeated_decisions_tasks_and_worlds_fit_without_losing_evidence(self):
        tasks=[dict(id=i,skill='defend_base',count=1,state='running',reason='standing defence order',
                    workflow=dict(args=dict(base='home',units='all',count=32,reason='守住受袭基地'),
                                  unit_tags=list(range(32)),issued_quantity=1,observed_completed=0))
               for i in range(8)]
        worlds=[dict(game_s=25*i,minerals=i*37,own={'Marine':20},
                     bases=[dict(tag=123,workers=16,ideal_workers=16,progress=1,energy=0,energy_max=200,
                                 type='OrbitalCommand')]) for i in range(64)]
        diagnostics=dict(errors=[],timeline=[dict(world=w,active_tasks=[dict(t,reason=t['reason']+str(i)) for t in tasks],
                        last_decision=dict(request_id=i//3,reason='守住基地并补兵'*30)) for i,w in enumerate(worlds)])
        class Client:
            payload=None
            def __init__(self,*args):pass
            def close(self):pass
            def chat(self,system,user):
                self.__class__.payload=json.loads(user)
                self.__class__.size=len(user)
                return '{"tasks":[],"category":"strategy","strategy":"按新的观测重新评估进攻时机"}'
        budget=RequestBudget(2)
        with patch('arena.autonomous_review.ChatGPTSubscriptionPlanner',Client):
            review_game('chatgpt/gpt-6-luna','/tmp/unused',budget,diagnostics,'候选战术','hard')
        self.assertLessEqual(Client.size,60000)
        restored=decode_tables(Client.payload)['evidence']
        expected=decode_tables(compact_review_evidence(diagnostics,[]))
        for key in expected:self.assertEqual(restored[key],expected[key])
        self.assertEqual(budget.used,1)

    def test_reference_shaped_original_values_roundtrip_and_cycles_reject(self):
        original=[{'$review_ref':5}]*30
        self.assertEqual(decode_tables(object_pool(original)),original)
        with self.assertRaisesRegex(ValueError,'cyclic'):
            decode_tables(dict(encoding='review_object_pool_v1',objects=[{'x':{'$review_ref':0}}],data={'$review_ref':0}))


if __name__=='__main__':unittest.main()
