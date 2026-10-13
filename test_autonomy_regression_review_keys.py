import json
import unittest
from unittest.mock import patch

from arena.autonomous_bot import RequestBudget
from arena.autonomous_review import review_game
from arena.context_tables import decode_tables, key_table


class ReviewKeys(unittest.TestCase):
    def test_large_distinct_execution_evidence_fits_without_dropping_fields(self):
        details={f'producer_{i}':dict(observed_completed=i,issued_quantity=i+1,
            currently_available_units=i+2,production_wait_reason=f'resource deficit {i}')
            for i in range(600)}
        event=dict(id=1,kind='execution_failure',error='real error',details=details)
        diagnostics=dict(errors=[event],timeline=[])
        class Client:
            def __init__(self,*args):pass
            def close(self):pass
            def chat(self,system,user):
                self.__class__.payload=json.loads(user)
                self.__class__.size=len(user)
                return '{"tasks":[],"category":"strategy","strategy":"基于实际观察调整计划"}'
        budget=RequestBudget(2)
        with patch('arena.autonomous_review.ChatGPTSubscriptionPlanner',Client):
            with patch('arena.context_tables.key_table',side_effect=lambda value:value):
                with self.assertRaisesRegex(ValueError,'context limit'):
                    review_game('chatgpt/gpt-6-luna','/tmp/unused',budget,diagnostics,'候选','hard')
            self.assertEqual(budget.used,0)
            review_game('chatgpt/gpt-6-luna','/tmp/unused',budget,diagnostics,'候选','hard')
        self.assertLessEqual(Client.size,60000)
        self.assertEqual(decode_tables(Client.payload)['evidence']['errors'],[event])
        self.assertEqual(budget.used,1)

    def test_original_numeric_keys_and_nested_values_roundtrip(self):
        original={str(i):{'long_field_name':i,'another_long_field_name':{'0':str(i)}}
                  for i in range(40)}
        self.assertEqual(decode_tables(key_table(original)),original)
        for index in ('00','-1','9','１'):
            with self.assertRaisesRegex(ValueError,'key index'):
                decode_tables(dict(encoding='review_key_table_v1',keys=['field'],data={index:1}))


if __name__=='__main__':unittest.main()
