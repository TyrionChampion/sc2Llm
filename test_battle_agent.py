"""Synthetic observations and mock model replies; native/paid evidence is separate."""
import json
import math
import tempfile
import time
import unittest
from dataclasses import replace
from types import SimpleNamespace

from arena.battle_agent import BattleAgent
from arena.tasks import TaskQueue
from arena.raw_engine import ATTACK
from arena.commander import Commander
from arena.deepseek_planner import DeepSeekFlashPlanner
from test_rally_orders import engine_backend
from test_realtime_runtime import unit,world


class Idle:
    def ready_for_request(self):return False
    def chat(self,*a):raise AssertionError('no model request expected')


class BattleTests(unittest.TestCase):
    def setUp(self):
        _,self.b=engine_backend('terran',(ATTACK,3673))
        self.q=TaskQueue(self.b,0,lambda _:None)
        self.events=[];self.audits=[];self.now=100.
        self.t=BattleAgent(self.b,0,SimpleNamespace(grid=self.b.grid),
            lambda kind,**kw:self.events.append(dict(kind=kind,**kw)),self.audits.append,Idle(),self.q,clock=lambda:self.now)
        self.addCleanup(self.t.close)
        self.bases=[unit(1,18,30,30,is_building=True),unit(2,18,60,30,is_building=True),unit(3,18,120,30,is_building=True)]
        self.army=[unit(i,48,80,90,ground_dps=7,air_dps=7,attack_range=6) for i in range(10,16)]
        self.enemy=unit(99,48,120,34,alliance=4,ground_dps=7,attack_range=6)

    def w(self,enemies=True,sec=20,army=None):return world(self.bases+(self.army if army is None else army)+([self.enemy] if enemies else []),sec)

    def reply(self,orders=None,**kw):
        groups,_=self.t._squads(self.army)
        sid=next(iter(groups))
        plan=dict(orders=[dict(squad=sid,mode='defend',target='base_3',reason='远处主力支援受袭三矿')] if orders is None else orders,reason='三矿遇袭，直接增援')
        self.t.outbox.put(dict(request_id=1,generation=self.t.generation,wall=100.,game_s=20.,
            response=json.dumps(plan,ensure_ascii=False),error=None,latency_s=1.,usage=None,
            squads={k:[u.tag for u in us] for k,us in groups.items()},**kw))

    def test_no_script_dispatch_while_model_waits_then_remote_army_goes_to_third(self):
        self.assertEqual(self.t.tick(self.w()),[])
        self.assertEqual(self.t.assignments,{})
        self.reply();self.now=101.
        actions=self.t.tick(self.w(sec=21))
        self.assertEqual(len(actions),1)
        c=actions[0].action_raw.unit_command
        self.assertEqual(set(c.unit_tags),set(range(10,16)))
        self.assertLess(math.dist((c.target_world_space_pos.x,c.target_world_space_pos.y),(120,30)),7)
        self.assertGreater(math.dist((c.target_world_space_pos.x,c.target_world_space_pos.y),(30,30)),60)
        self.assertEqual(self.t.accepted,1)
        self.assertEqual(self.t.action_contexts[0]['decision_source'],'battle_llm')

    def test_enemy_motion_does_not_expire_still_valid_defense(self):
        self.t.tick(self.w());self.reply();self.now=101.
        self.enemy=replace(self.enemy,x=125)
        self.assertTrue(self.t.tick(self.w(sec=21)))
        self.assertEqual(self.t.expired,0)
        self.assertEqual(self.t.accepted,1)

    def test_protected_army_cannot_be_seized_from_an_inflight_reply(self):
        self.t.tick(self.w());self.reply();self.now=101.
        self.assertEqual(self.t.tick(self.w(sec=21),{10}),[])
        self.assertEqual(self.t.accepted,0)
        self.assertEqual(self.t.errors,1)
        self.assertFalse(self.t.assignments)

    def test_old_reply_does_not_dispatch_after_threat_clears(self):
        self.t.tick(self.w());self.reply();self.now=107.
        self.assertEqual(self.t.tick(self.w(enemies=False,sec=27)),[])
        self.assertEqual(self.t.accepted,0)
        self.assertFalse(self.t.assignments)

    def test_overdue_reply_and_changed_strategy_are_rejected(self):
        self.t.tick(self.w());self.reply();self.now=114.
        self.assertEqual(self.t.tick(self.w(sec=21)),[])
        self.assertEqual(self.t.expired,1)

    def test_model_failure_does_not_fall_back_to_scripted_reinforcement(self):
        self.t.tick(self.w());self.reply();r=self.t.outbox.get_nowait();r['error']='TimeoutError: official API timeout';self.t.outbox.put(r)
        self.now=101.;self.assertEqual(self.t.tick(self.w(sec=21)),[])
        self.assertEqual(self.t.errors,1)
        self.assertTrue(any(e['kind']=='tactical_plan_rejected' and 'timeout' in e['error'] for e in self.events))

    def test_plan_is_atomic_and_cannot_start_unauthorized_attack(self):
        self.t.tick(self.w());self.reply(orders=[
            dict(squad='squad_10',mode='defend',target='base_3',reason='防守'),
            dict(squad='unknown',mode='attack',target=[170,170],reason='进攻')])
        self.now=101.;self.assertEqual(self.t.tick(self.w(sec=21)),[])
        self.assertFalse(self.t.assignments)
        self.assertEqual(self.t.errors,1)

    def test_observed_arrival_is_reported_without_spamming_orders(self):
        self.t.tick(self.w());self.reply();self.now=101.;self.t.tick(self.w(sec=21))
        army=[replace(u,x=120,y=30,orders=(ATTACK,)) for u in self.army]
        self.now=102.;self.assertEqual(self.t.tick(self.w(sec=22,army=army)),[])
        self.assertTrue(any(e['kind']=='tactical_arrived' for e in self.events))

    def test_ability_or_path_failure_is_visible_no_substitute_target(self):
        self.t.tick(self.w());self.reply();self.now=101.
        self.b.path_distances=lambda i,start,points:[0]*len(points)
        self.assertEqual(self.t.tick(self.w(sec=21)),[])
        self.assertTrue(all(a['failed'] for a in self.t.assignments.values()))
        self.assertTrue(any(e['kind']=='tactical_order_failed' and 'unreachable' in e['error'] for e in self.events))

    def test_real_mailbox_builds_local_payload_and_triggered_request(self):
        class Capture:
            def chat(inner,system,prompt):
                inner.p=json.loads(prompt);inner.system=system
                return json.dumps(dict(orders=[dict(squad=inner.p['squads'][0]['id'],mode='defend',
                    target=inner.p['actual_alerts'][0]['base'],reason='从远处支援三矿')],reason='保护第三基地'))
        capture=Capture();self.t.client=capture
        self.assertEqual(self.t.tick(self.w(enemies=False)),[])
        self.assertEqual(self.t.requests,0)
        self.assertEqual(self.t.tick(self.w(sec=21)),[])
        until=time.monotonic()+2
        while self.t.outbox.empty() and time.monotonic()<until:time.sleep(.005)
        self.assertFalse(self.t.outbox.empty())
        self.now=101.
        self.assertTrue(self.t.tick(self.w(sec=22)))
        self.assertEqual(capture.p['actual_alerts'][0]['base'],'base_3')
        self.assertGreater(capture.p['squads'][0]['distance_to_bases']['base_3'],50)
        self.assertEqual(self.t.requests,1)

    def test_commander_installs_one_battle_agent_with_explicit_client(self):
        self.b.initial=[self.w(enemies=False)];self.b.register_action_context=lambda *a:None
        with tempfile.TemporaryDirectory() as root:
            c=Commander(self.b,0,Idle(),opening=False,tactics_enabled=True,tactical_client=Idle(),feedback_dir=root)
            try:
                self.assertIsInstance(c.tactics,BattleAgent)
                self.assertIsNone(c.defense)
                self.assertFalse(c.reflex.combat_enabled)
                self.assertEqual(c.control_policy['army_tactics']['kind'],'independent_battle_llm')
                self.assertIn('direct recall of distant',c.system)
            finally:c.stop()

    def test_public_battle_reply_and_failed_dispatch_are_visible(self):
        from llm_output_window import Feed
        f=Feed();f.consume(dict(record='tactical_request_finished',request_id=3,game_s=20,latency_s=2,
            response=json.dumps(dict(orders=[dict(squad='squad_10',mode='defend',target='base_3',reason='增援三矿')],reason='三矿受袭'))))
        self.assertIn('增援防守',f.entries[-1]['body'])
        self.assertIn('base_3',f.entries[-1]['body'])
        f.consume(dict(record='feedback_event',event=dict(kind='tactical_order_failed',game_loop=448,error='unreachable',reason='未发出')))
        self.assertTrue(f.entries[-1]['error']);self.assertIn('unreachable',f.entries[-1]['body'])

    def test_battle_client_uses_non_thinking_short_reply_budget(self):
        c=DeepSeekFlashPlanner(battle=True);requests=[]
        c._post=lambda url,body,headers:(requests.append(body) or dict(model='deepseek-flash',choices=[dict(finish_reason='stop',message={'content':'{}'})]))
        c._call_openai('system','{}')
        self.assertEqual(requests[0]['thinking']['type'],'disabled')
        self.assertEqual(c.timeout,10)
        self.assertEqual(c.max_tokens,1200)

    def test_prompt_keeps_a_long_match_stable_prefix_for_cache(self):
        """前缀缓存只对"从开头就逐字相同"的部分生效;易变字段必须排在后面。
        2026-10-09 那局实测:旧的序列化顺序把 game_s 放在第一个字段,相邻请求的公共
        前缀中位数只有 13 字符(命中率 4~7%)。这里钉住"固定头足够长且确实稳定"。
        """
        prompts = []
        class Capture:
            def chat(inner, system, prompt):
                prompts.append(prompt)
                return json.dumps(dict(orders=[], reason='保持'))
        self.t.client = Capture()
        # 同一局内两次真实请求:第一次带警报,第二次是回执后的复核(易变状态已变,固定头不变)
        self.assertEqual(self.t.tick(self.w(sec=21)), [])
        until = time.monotonic() + 2
        while self.t.outbox.empty() and time.monotonic() < until: time.sleep(.005)
        self.now = 101.
        self.t.tick(self.w(sec=22))
        until = time.monotonic() + 2
        while len(prompts) < 1 and time.monotonic() < until: time.sleep(.005)
        self.t.next_review = 0; self.t.dirty = True
        self.now = 200.
        self.t.tick(self.w(sec=23))
        until = time.monotonic() + 2
        while len(prompts) < 2 and time.monotonic() < until: time.sleep(.005)
        self.assertEqual(len(prompts), 2, '两次触发都应发出请求')
        shared = BattleAgent._shared_prefix(prompts[0], prompts[1]) if hasattr(BattleAgent, '_shared_prefix') else None
        if shared is None:
            n = min(len(prompts[0]), len(prompts[1])); shared = 0
            while shared < n and prompts[0][shared] == prompts[1][shared]: shared += 1
        head = prompts[0].index('"game_s"') if '"game_s"' in prompts[0] else 0
        self.assertGreater(head, 400, '固定头(易变字段之前的部分)应当足够长: %d' % head)
        self.assertGreaterEqual(shared, head - 1, '公共前缀应覆盖整个固定头: shared=%d head=%d' % (shared, head))
        self.assertTrue(prompts[0].startswith('{"map_size"'), '稳定字段必须在最前面')
        payload = json.loads(prompts[0])
        self.assertIn('actual_alerts', payload)
        self.assertIn('visible_enemies', payload)

    def test_account_level_http_error_is_fatal_and_not_retried(self):
        """402/401/403 是关级拒绝:重试无用,必须立刻冒泡,而不是每 6 秒重打一次。
        2026-10-10 那局实测:15 分钟余额耗尽后双方仍继续尝试到 17 分钟,共 78 次失败。
        """
        from arena.llm import LLMClient, http_error, FATAL_HTTP_CODES
        self.assertIn(402, FATAL_HTTP_CODES)
        self.assertIn(401, FATAL_HTTP_CODES)
        error = http_error(402, '{"error":{"message":"Insufficient Balance"}}')
        self.assertTrue(error.fatal)
        self.assertEqual(error.status_code, 402)
        self.assertFalse(http_error(500, 'server error').fatal)
        self.assertFalse(http_error(429, 'rate limited').fatal)
        client = LLMClient('deepseek')
        # 只替换 provider 对象让 configured 为真;不触碰真实网络或凭据
        client.p = SimpleNamespace(name='deepseek', kind='openai', api_key='test-key',
                                   base_url='http://127.0.0.1:9', model='deepseek-flash',
                                   extra_headers={}, key_required=True, configured=True)
        attempts = []
        def post(*args):
            attempts.append(1)
            raise http_error(402, '{"error":{"message":"Insufficient Balance"}}')
        client._call_openai = post  # 实例级替换,避免污染其他测试
        with self.assertRaises(Exception) as caught:
            client.chat('s', 'u', retries=2)
        self.assertEqual(len(attempts), 1, '关级错误不得重试')
        self.assertTrue(getattr(caught.exception, 'fatal', False), 'fatal 标记必须向上传递')
        self.assertIn('402', str(caught.exception))

if __name__=='__main__':unittest.main()
