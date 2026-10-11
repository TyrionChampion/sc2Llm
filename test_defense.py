"""Synthetic observations + production runtime contracts, not a native match."""
import json
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from unittest.mock import patch
from arena.commander import Commander
from arena.defense import DefenseCoordinator
from arena.deepseek_planner import DeepSeekFlashPlanner
from arena.raw_engine import ATTACK, MOVE, CC, MARINE, SCV
from arena.tasks import TaskQueue, Task
from arena.python_skills import PythonSkillRun
from test_realtime_runtime import Backend, unit, world
from test_python_skills import entry, ARGS

class Client:
    def __init__(self,gate=None,answer=None):
        self.gate=gate;self.calls=0;self.prompts=[]
        self.answer=answer or '{"mode":"defend","base_tag":1,"reason":"保护基地"}'
    def chat(self,system,user):
        self.calls+=1;self.prompts.append(json.loads(user))
        if self.gate:self.gate.wait(3)
        return self.answer

def units(enemy=True):
    data=[unit(1,CC,is_building=True),unit(2,MARINE,ground_dps=10,air_dps=10),unit(4,SCV)]
    if enemy:data.append(unit(3,MARINE,36,30,4,ground_dps=10))
    return data

class DefenseTests(unittest.TestCase):
    def setUp(self):
        self.events=[];self.audit=[];self.clock=[0.]
        self.client=Client();self.q=TaskQueue(Backend(),0,lambda s:None,
            lambda k,**v:self.events.append(dict(kind=k,**v)))
        self.d=DefenseCoordinator(self.client,self.q,lambda k,**v:self.events.append(dict(kind=k,**v)),self.audit.append,clock=lambda:self.clock[0])
    def tearDown(self):self.d.close();self.q.close()
    def tick(self,sec,items=None):
        self.clock[0]=sec
        return self.d.tick(world(units() if items is None else items,sec))
    def drain(self):
        end=time.monotonic()+1
        while self.d.outbox.empty() and time.monotonic()<end:time.sleep(.002)
        self.assertFalse(self.d.outbox.empty())

    def test_first_order_needs_neither_main_nor_defender_reply(self):
        gate=threading.Event();self.client.gate=gate
        try:
            a=self.tick(0)
            self.assertEqual(a[0].action_raw.unit_command.ability_id,ATTACK)
            self.assertEqual(set(a[0].action_raw.unit_command.unit_tags),{2})
            self.assertNotIn(4,self.d.tags);self.assertEqual(self.d.first_order_s,0)
            self.assertTrue(self.d.thinking)
        finally:gate.set()

    def test_no_peace_time_requests_or_no_troops_paid_polling(self):
        for t in range(20):self.tick(t,units(False))
        self.assertEqual(self.d.requests,0)
        self.tick(20,[u for u in units() if u.tag!=2])
        self.assertTrue(self.d.active);self.assertEqual(self.d.requests,0)
        self.assertIn('defense_no_units',[e['kind'] for e in self.events])

    def test_defender_accepts_chinese_public_reply(self):
        self.tick(0);self.drain();self.tick(1)
        self.assertEqual(self.d.accepted,1)
        self.assertEqual(self.d.last_decision['reason'],'保护基地')
        self.assertTrue(any(r['record']=='defense_request_finished' for r in self.audit))

    def test_delayed_reply_discarded_guard_continues(self):
        self.tick(0);self.drain();a=self.tick(8)
        self.assertEqual(self.d.accepted,0);self.assertEqual(self.d.expired,1)
        self.assertTrue(a);self.assertIn('defense_plan_expired',[e['kind'] for e in self.events])

    def test_release_is_hysteretic_and_late_reply_cannot_retake(self):
        self.tick(0);self.drain()
        self.tick(1,units(False));self.assertTrue(self.d.active)
        a=self.tick(7,units(False))
        self.assertFalse(self.d.active);self.assertEqual(a[0].action_raw.unit_command.ability_id,4)
        self.assertEqual(self.q.defense_tags,{2})
        self.tick(8,units(False));self.assertFalse(self.q.defense_tags)
        self.assertEqual(self.d.generation,2)

    def test_unknown_or_unattacked_base_rejected(self):
        self.client.answer='{"mode":"defend","base_tag":999,"reason":"坏目标"}'
        self.tick(0);self.drain();self.tick(1)
        self.assertEqual(self.d.errors,1);self.assertIn('base_tag',self.d.last_error)
        self.assertEqual(self.d.accepted,0)

    def test_json_failure_visible_backoff_no_repeated_immediate_call(self):
        self.client.answer='invalid'
        self.tick(0);self.drain();self.tick(1)
        self.tick(4);self.assertEqual(self.d.requests,1)
        self.assertTrue(self.d.last_error);self.assertEqual(self.d.mode,'defend')

    def test_air_enemy_not_chased_by_ground_only(self):
        data=units();data[1]=replace(data[1],air_dps=0)
        data[-1]=replace(data[-1],flying=True)
        a=self.tick(0,data)
        self.assertTrue(all(x.action_raw.unit_command.ability_id!=ATTACK for x in a))

    def test_offscreen_visible_enemy_triggers_not_enemy_start(self):
        a=self.tick(0)
        self.assertFalse(units()[-1].on_screen)
        self.assertTrue(a)
        self.assertLess(a[0].action_raw.unit_command.target_world_space_pos.x,60)

    def test_second_base_and_damage_without_visible_attacker(self):
        data=units(False)+[unit(8,CC,90,90,is_building=True)]
        self.tick(0,data)
        data=[replace(u,health=30) if u.tag==8 else replace(u,x=90,y=90) if u.tag==2 else u for u in data]
        a=self.tick(1,data)
        self.assertTrue(self.d.active);self.assertEqual([u.tag for u in self.d.damaged],[8]);self.assertEqual(self.d.base_tag,8)
        self.assertTrue(all(x.action_raw.unit_command.ability_id!=ATTACK for x in a))

    def test_macro_cannot_override_owned_units(self):
        self.q.enqueue([{'skill':'move_out'}],0)
        self.tick(0)
        actions=self.q.tick(world(units()),self.d.tags)
        self.assertEqual(actions,[]);self.assertTrue(self.q.tasks[0].active)

    def python_task(self):
        p=PythonSkillRun(entry("def tick(c,s): return {}"),dict(ARGS))
        p.tags={2};p.mission={'deadline_game_s':30};p.state={'n':4}
        p.worker=unittest.mock.Mock()
        task=Task(9,'fixture',program=p,status='running',created=0)
        self.q.tasks.append(task);return task,p

    def test_pause_and_resume_discards_old_worker_preserves_time(self):
        task,p=self.python_task();old=p.worker
        self.tick(2);old.close.assert_called_once();self.assertIsNone(p.worker)
        self.assertEqual(task.status,'paused')
        self.q.tick(world(units(),3),self.d.tags)
        self.tick(9,units(False))
        with patch('arena.python_skills.IsolatedWorker') as worker:
            self.tick(10,units(False))
            self.assertIs(p.worker,worker.return_value)
            self.assertEqual(task.status,'running');self.assertAlmostEqual(task.created,world([],10).seconds-world([],2).seconds)
            self.assertAlmostEqual(p.mission['deadline_game_s'],30+world([],10).seconds-world([],2).seconds)
            self.assertEqual(p.state,{'n':4});self.assertIsNone(p.sent_loop)

    def test_loss_during_defense_requests_redispatch_not_source_repair(self):
        task,p=self.python_task()
        self.tick(0)
        self.tick(7,[u for u in units(False) if u.tag!=2])
        self.tick(8,[u for u in units(False) if u.tag!=2])
        self.assertEqual(task.status,'cancelled')
        self.assertIn('defense_task_needs_redispatch',[e['kind'] for e in self.events])
        self.assertNotIn('python_skill_failed',[e['kind'] for e in self.events])

    def test_pending_cancel_stop_cannot_override_defense(self):
        task,p=self.python_task();self.tick(0)
        self.q.pending_stops.append(task);self.q.update(task,'cancelled','test cancel')
        a=self.q.tick(world(units(),1),self.d.tags)
        self.assertEqual(a,[])
        self.assertIn('defense_order_blocked',[e['kind'] for e in self.events])

    def test_prompt_bounded_under_large_battle(self):
        data=units()+[unit(100+i,MARINE,35,30,4,ground_dps=10) for i in range(500)]
        self.tick(0,data);payload=self.d._payload(world(data))
        self.assertEqual(len(payload['enemies']),16)
        self.assertEqual(payload['omitted']['enemies'],485)
        self.assertLess(len(json.dumps(payload)),8000)


    def test_sieged_tank_is_not_forced_to_walk(self):
        a=self.tick(0,[u for u in units() if u.tag!=2]+[unit(10,32,ground_dps=30)])
        self.assertEqual(a,[]);self.assertFalse(self.d.tags)

    def test_player_instances_do_not_share_leases_or_messages(self):
        other=DefenseCoordinator(Client(),TaskQueue(Backend(),0,lambda s:None),
                                 lambda *a,**k:None,lambda x:None,clock=lambda:0)
        try:
            self.tick(0)
            other.tick(world(units(False)))
            self.assertEqual(other.tags,set());self.assertEqual(other.requests,0)
        finally:other.close()

    def test_withdraw_hysteresis(self):
        self.client.answer='{"mode":"withdraw","base_tag":1,"reason":"后撤防守"}'
        self.tick(0);self.drain();self.tick(1)
        self.assertEqual(self.d.mode,'withdraw')
        self.client.answer='{"mode":"defend","base_tag":1,"reason":"回防"}'
        self.tick(3.1);self.drain();self.tick(4)
        self.assertEqual(self.d.mode,'withdraw')
        self.assertIn('defense_mode_deferred',[e['kind'] for e in self.events])

class CommanderDefenseTests(unittest.TestCase):
    def test_main_blocked_while_defense_responds_and_main_plan_is_stale(self):
        gate=threading.Event();main=Client(gate,'{"tasks":[{"skill":"move_out"}],"reason":"进攻"}')
        defense=Client()
        b=Backend();b.initial=[world(units(False))]
        with tempfile.TemporaryDirectory() as tmp:
            c=Commander(b,0,main,opening=False,defense_client=defense,feedback_dir=tmp)
            try:
                c.tick(b.initial[0]);start=time.perf_counter()
                a=c.tick(world(units(),1));duration=time.perf_counter()-start
                self.assertLess(duration,.1);self.assertTrue(c.thinking)
                self.assertTrue(any(x.action_raw.unit_command.ability_id==ATTACK for x in a))
                gate.set();limit=time.monotonic()+1
                while c.outbox.empty() and time.monotonic()<limit:time.sleep(.002)
                c.tick(world(units(),2))
                self.assertEqual(c.plans_rejected,1)
                self.assertEqual(c.interval,25);self.assertFalse(c.reflex.combat_enabled)
                self.assertTrue(c.stats()['defense_adjutant']['enabled'])
            finally:gate.set();c.stop()


    def test_viewer_defense_response_does_not_overwrite_main_request(self):
        from llm_output_window import Feed
        f=Feed();f.consume(dict(record='request_queued',request_id=1,prompt={'world':{'game_s':1}}))
        f.consume(dict(record='defense_request_finished',request_id=1,response='{"mode":"hold","base_tag":1,"reason":"守住坡口"}',latency_s=.5))
        self.assertEqual(f.pending,1);self.assertIn('守住坡口',f.entries[-1]['body'])
        f.consume(dict(record='feedback_event',event=dict(kind='defense_plan_expired',game_loop=100,request_id=1)))
        self.assertTrue(f.entries[-1]['error']);self.assertNotIn(1,f.plans)

    def test_official_request_disables_thinking_and_is_small(self):
        c=DeepSeekFlashPlanner(defense=True);sent={}
        def post(url,data,headers):
            sent.update(data)
            return dict(model='deepseek-flash',choices=[dict(finish_reason='stop',message=dict(content='{}'))])
        with patch.object(c,'_post',post):c._call_openai('s','u')
        self.assertEqual(sent['thinking'],{'type':'disabled'})
        self.assertNotIn('reasoning_effort',sent);self.assertEqual(sent['max_tokens'],384)
        self.assertEqual(c.timeout,8)

if __name__=='__main__':unittest.main()
