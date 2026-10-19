"""Cross-round offensive intent, real request paths and atomic validation; no API."""
from copy import deepcopy
from dataclasses import replace
import json
import tempfile
import unittest

import jsonschema

from arena.autonomous_bot import BotPlanner, RequestBudget, INITIAL_STRATEGY, economical_payload
from arena.battle_plans import output_schema
from arena.chatgpt_planner import battle_output_schema
from arena.commander import Commander
from arena.context_tables import decode_tables
from arena.go_schema import battle_schema
from arena.offensive_policy import OffensiveMemory, validate_plan
from test_realtime_runtime import Backend, unit, world


def commitment(action='prepare', **changes):
    result=dict(action=action,objective='敌远处分矿',squad='12跳虫；其余留守',
        launch_condition='侦察该矿，若见工人且守军少于4则袭击',
        abort_condition='见4名守军则撤到[54,115]',review_after_s=30,
        revision_reason='补满小队后施压，避免等待全图视野')
    result.update(changes)
    return result


class Capture:
    compact_battle=True
    soft_timeout=5
    timeout=10
    model='opencode-go/deepseek-v4.1-flash'
    calls=0
    attempts=0
    closed=False
    last_metadata={}

    def __init__(self, replies=None):
        self.replies=list(replies or [])
        self.requests=[]

    def chat(self,system,user):
        self.requests.append((system,json.loads(user)))
        self.calls+=1;self.attempts+=1
        return json.dumps(self.replies.pop(0) if self.replies else dict(tasks=[],reason='继续当前计划'),ensure_ascii=False)


class OffensivePolicyTests(unittest.TestCase):
    def test_deadline_and_exact_conditions_survive_omission_null_and_snapshot_mutation(self):
        memory=OffensiveMemory();plan=commitment()
        memory.accept(dict(tasks=[],offensive_plan=plan),100,1)
        plan['objective']='不能修改已接受计划'
        for value in ({'tasks':[]},{'tasks':[],'offensive_plan':None}):memory.accept(value,150,2)
        report=memory.public(150)
        self.assertEqual(report['commitment']['plan']['objective'],'敌远处分矿')
        self.assertEqual(report['commitment']['review_at_game_s'],130)
        self.assertTrue(report['commitment']['review_due'])
        report['commitment']['plan']['abort_condition']='不能污染内存'
        self.assertIn('[54,115]',memory.public(151)['commitment']['plan']['abort_condition'])

    def test_repeated_waits_cannot_postpone_first_review_even_with_changed_goals(self):
        memory=OffensiveMemory()
        for i in range(10):
            memory.accept(dict(tasks=[],offensive_plan=commitment(objective='新目标'+str(i))),100+i*20,i)
        report=memory.public(280)
        self.assertEqual(report['unresolved_wait']['first_review_at_game_s'],130)
        self.assertEqual(report['unresolved_wait']['elapsed_game_s'],180)
        self.assertTrue(report['unresolved_wait']['review_due'])
        self.assertEqual(len(report['recent']),3)
        # A declaration of attack by itself does not claim a task request.
        memory.accept(dict(tasks=[],offensive_plan=commitment('attack')),290,11)
        self.assertIsNotNone(memory.public(290)['unresolved_wait'])

    def test_attack_request_clears_wait_without_claiming_dispatch_or_victory(self):
        for task in (dict(skill='raid'),dict(skill='drop_raid'),dict(skill='move_out'),
                     dict(skill='attack_nearest'),dict(skill='order_units',args=dict(mode='attack_move'))):
            memory=OffensiveMemory();memory.accept(dict(tasks=[],offensive_plan=commitment()),0,1)
            memory.accept(dict(tasks=[task]),80,2)
            report=memory.public(80)
            self.assertIsNone(report['unresolved_wait'])
            self.assertEqual(report['last_attack_request_game_s'],80)
            self.assertNotIn('victory',report)
            self.assertEqual(report['commitment']['request_id'],1)

    def test_all_provider_schemas_share_the_optional_metadata_contract(self):
        payload=dict(offensive_policy=dict(enabled=True))
        for builder in (output_schema,battle_output_schema,battle_schema):
            schema=builder(payload)
            for value in (commitment(),None):
                jsonschema.validate(dict(tasks=[],reason='试探并设置撤退条件',offensive_plan=value),schema)
            bad=commitment(review_after_s=181)
            with self.assertRaises(jsonschema.ValidationError):
                jsonschema.validate(dict(tasks=[],reason='无界等待',offensive_plan=bad),schema)
        # Existing callers that do not advertise this feature keep their schema.
        self.assertEqual(battle_output_schema({})['required'],['tasks','reason'])

    def test_nonfinite_bool_missing_or_extra_fields_are_rejected(self):
        invalid=[commitment(review_after_s=x) for x in (True,0,float('nan'),float('inf'),181)]
        invalid += [commitment(squad=' '),commitment(action='rush')]
        extra=commitment();extra['execute_automatically']=True;invalid.append(extra)
        missing=commitment();missing.pop('abort_condition');invalid.append(missing)
        for plan in invalid:
            with self.subTest(plan=plan),self.assertRaises(ValueError):validate_plan(plan)

    def test_context_pressure_removes_only_history_not_current_wait_or_conditions(self):
        memory=OffensiveMemory()
        for i in range(6):memory.accept(dict(tasks=[],offensive_plan=commitment()),i*30,i)
        policy=memory.public(200)
        payload=dict(world=dict(game_s=200),offensive_policy=policy)
        result=decode_tables(economical_payload(payload,threshold=1))['offensive_policy']
        self.assertEqual(result['commitment'],policy['commitment'])
        self.assertEqual(result['unresolved_wait'],policy['unresolved_wait'])
        self.assertEqual(len(policy['recent']),3)

    def commander(self,client):
        backend=Backend()
        backend.initial=[world([unit(1,18,is_building=True),unit(2,48,ground_dps=10)])]
        backend.register_action_context=lambda *args:None
        directory=tempfile.TemporaryDirectory();self.addCleanup(directory.cleanup)
        commander=Commander(backend,0,client,opening=False,feedback_dir=directory.name)
        self.addCleanup(commander.stop)
        commander.defer_planner_requests=True
        return commander,backend.initial[0]

    def consume(self,c,w,plan,request_id=1):
        c.outbox.put((w.seconds,deepcopy(plan),None,0,True,json.dumps(plan),request_id,[]))
        return c.tick(w)

    def test_invalid_metadata_or_tasks_do_not_replace_accepted_commitment(self):
        c,w=self.commander(Capture())
        self.consume(c,w,dict(tasks=[],reason='暂等成军',offensive_plan=commitment()))
        original=c.offensive_memory.public(0)
        before=len(c.tasks.tasks)
        self.consume(c,w,dict(tasks=[dict(skill='train_worker')],reason='错误期限',offensive_plan=commitment(review_after_s=True)),2)
        self.assertEqual(len(c.tasks.tasks),before)
        self.consume(c,w,dict(tasks=[dict(skill='nonexistent_command')],reason='错误任务',offensive_plan=commitment('attack')),3)
        self.assertEqual(c.offensive_memory.public(0),original)
        self.assertEqual(c.decisions,1)

    def test_actual_next_commander_request_contains_expired_commitment_without_extra_orders(self):
        client=Capture([dict(tasks=[],reason='先试探',offensive_plan=commitment()),dict(tasks=[],reason='继续合理防守')])
        c,w=self.commander(client)
        self.assertTrue(c.request_plan(w))
        reply=c.outbox.get(timeout=3);c.outbox.put(reply)
        self.assertEqual(c.tick(w),[])
        later=replace(w,game_loop=80*224//10)
        self.assertEqual(c.tick(later),[])
        self.assertEqual(client.calls,1,'plan expiry must not bypass the scheduler or call the model')
        self.assertTrue(c.request_plan(later))
        reply=c.outbox.get(timeout=3);c.outbox.put(reply)
        policy=client.requests[-1][1]['offensive_policy']
        self.assertEqual(policy['commitment']['plan'],commitment())
        self.assertTrue(policy['unresolved_wait']['review_due'])
        self.assertEqual(c.tasks.tasks,[])

    def test_bounded_go_real_prompt_uses_shared_policy_and_preserves_user_advice(self):
        capture=Capture()
        planner=BotPlanner(capture,RequestBudget(8),INITIAL_STRATEGY)
        memory=OffensiveMemory();memory.accept(dict(tasks=[],offensive_plan=commitment()),0,1)
        advice=dict(messages=[dict(id=7,text='先守二矿，等对手回防后再袭矿')])
        planner.chat('this system is replaced',json.dumps(dict(world=dict(game_s=60,race='zerg',own={}),
            offensive_policy=memory.public(60),operator_advice=advice)))
        system,payload=capture.requests[0]
        self.assertIn('offensive_plan',system)
        self.assertIn('不无限等待完美视野',system)
        self.assertIn('打赢局部交战后',system)
        self.assertEqual(payload['offensive_policy']['commitment']['plan'],commitment())
        self.assertEqual(payload['operator_advice'],advice)
        self.assertLessEqual(len(system)+len(json.dumps(payload,ensure_ascii=False,separators=(',',':'))),planner.context_limit)

    def test_large_go_request_keeps_current_plan_and_observations_within_original_guard(self):
        capture=Capture();planner=BotPlanner(capture,RequestBudget(8),'保持当前模型目标')
        memory=OffensiveMemory();memory.accept(dict(tasks=[],offensive_plan=commitment()),0,1)
        current=dict(game_s=60,current_units='x'*38000)
        planner.chat('',json.dumps(dict(world=current,offensive_policy=memory.public(60))))
        system,payload=capture.requests[0]
        self.assertEqual(decode_tables(payload)['world'],current)
        self.assertEqual(decode_tables(payload)['offensive_policy']['commitment']['plan'],commitment())
        self.assertLessEqual(planner.input_chars,45000)

    def test_structured_provider_keeps_raid_and_drop_tools_available_with_new_plan(self):
        payload=dict(offensive_policy=dict(enabled=True),control_policy=dict(spatial_missions_available=True))
        for builder in (battle_output_schema,battle_schema):
            schema=builder(payload)
            for kind in ('raid','drop_raid'):
                args=dict(unit='marine',count=4,retreat_target='rally',retreat_enemy_count=4,
                          threat_radius=10,dwell_s=20,timeout_s=180,reason='施压后撤回')
                if kind=='raid':args['targets']=['enemy_start']
                else:args.update(transport='medivac',target='enemy_start',waypoints=[])
                jsonschema.validate(dict(tasks=[dict(skill=kind,count=1,args=args)],reason='试探经济目标',
                                         offensive_plan=commitment('harass')),schema)


if __name__=='__main__':unittest.main()
