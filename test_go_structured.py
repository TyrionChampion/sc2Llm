import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import jsonschema
from arena.go_planner import perform_request, GO_MODEL, GoPlanner
from arena.go_schema import battle_schema
from arena.autonomous_bot import BotPlanner, RequestBudget, BudgetExhausted, contact_table
from arena.context_tables import decode_tables
from arena.tasks import TaskQueue
from test_realtime_runtime import Backend


class GoStructured(unittest.TestCase):
    def payload(self):
        return dict(control_policy=dict(spatial_missions_available=True),tech_tree=dict(race='terran',
            units=[dict(name='marine')],structures=[dict(name='barracks')],
            research=[dict(name='stimpack')],forms=['siege_tank_sieged']))

    def test_native_schema_prevents_observed_spatial_count_layer_error(self):
        schema=battle_schema(self.payload())
        def route(url,**kwargs):
            constrained=kwargs['json']['response_format'].get('type')=='json_schema'
            args=dict(unit='army',mode='move',target='home',timeout_s=120,reason='回防')
            if constrained:args['count']=3
            answer=dict(tasks=[dict(skill='order_units',count=1 if constrained else 3,args=args)],reason='回防')
            return SimpleNamespace(status_code=200,json=lambda:dict(model='deepseek-v4.1-flash',
                choices=[dict(finish_reason='stop',message=dict(content=json.dumps(answer)))]))
        request=dict(requested_model=GO_MODEL,system='test',observation='{}',timeout=60,response_schema=schema)
        with patch.dict(os.environ,{'open_api_key':'test-secret'}):
            result=perform_request(request,route)
        plan=json.loads(result['answer'])
        queue=TaskQueue(Backend(),0,lambda s:None)
        queue.enqueue(plan['tasks'],0)
        self.assertEqual(queue.tasks[0].program.args['count'],3)

    def test_schema_rejects_both_missing_inner_count_and_wrong_outer_count(self):
        schema=battle_schema(self.payload())
        jsonschema.Draft202012Validator.check_schema(schema)
        args=dict(unit='army',count=3,mode='move',target='home',timeout_s=120,reason='回防')
        plan=dict(tasks=[dict(skill='order_units',count=1,args=args)],reason='回防')
        jsonschema.validate(plan,schema)
        for task in (dict(skill='order_units',count=3,args=args),
                     dict(skill='order_units',count=1,args={k:v for k,v in args.items() if k!='count'})):
            with self.assertRaises(jsonschema.ValidationError):
                jsonschema.validate(dict(tasks=[task],reason='回防'),schema)

    def packet_client(self):
        class Client(GoPlanner):
            def __init__(self):
                self.model=GO_MODEL;self.response_kind='battle';self.structured_battle=True
                self.native_schema=True
                self.response_schema=None;self.calls=self.attempts=0;self.closed=False
                self.last_metadata=None
            def chat(self,system,user):
                self.calls+=1;self.attempts+=1;self.payload=json.loads(user)
                return '{"tasks":[],"reason":"格式检查"}'
        return Client()

    def test_valid_compact_technology_packet_keeps_world_and_feedback(self):
        payload=self.payload()
        entries=[dict(name=name,role='重复静态说明'*40) for name in ('marine','marauder','siege_tank')]
        payload['tech_tree']['units']=contact_table(entries)
        self.assertIsInstance(payload['tech_tree']['units'],dict)
        payload.update(world=dict(game_s=30,own={'Marine':2}),feedback=dict(events=[dict(id=1,error='真实拒绝')]))
        client=self.packet_client();planner=BotPlanner(client,RequestBudget(2),'模型自行选择战术')
        planner.chat('',json.dumps(payload))
        restored=decode_tables(client.payload)
        self.assertEqual(restored['world'],payload['world'])
        self.assertEqual(restored['feedback'],payload['feedback'])
        self.assertEqual([e['name'] for e in restored['tech_tree']['units']],[e['name'] for e in entries])
        self.assertGreater(planner.schema_transport_chars,0)

    def test_oversize_current_fact_stops_before_model_request(self):
        client=self.packet_client();budget=RequestBudget(2)
        planner=BotPlanner(client,budget,'模型自行选择战术')
        payload=self.payload();payload['world']=dict(current_fact='x'*planner.context_limit)
        with self.assertRaises(BudgetExhausted):planner.chat('',json.dumps(payload))
        self.assertEqual(budget.used,0)
        self.assertEqual(client.attempts,0)

    def test_provider_rejection_keeps_only_fixed_topics_not_echoed_values(self):
        response=SimpleNamespace(status_code=400,json=lambda:dict(error=dict(
            type='invalid_request_error',message='invalid schema: echoed-private-value')))
        request=dict(requested_model=GO_MODEL,system='test',observation='{}',timeout=60)
        with patch.dict(os.environ,{'open_api_key':'test-secret'}):
            result=perform_request(request,lambda *a,**kw:response)
        self.assertEqual(result['provider_error_topics'],['schema'])
        self.assertNotIn('echoed-private-value',json.dumps(result))
        self.assertNotIn('test-secret',json.dumps(result))


if __name__=='__main__':unittest.main()
