"""Deadline/state tests use controlled clocks/delays, not live match claims."""
import json
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from arena.opencode_bridge import OpenCodePlanner
from arena.commander import Commander
from arena.feedback import FeedbackJournal
from arena.tasks import TaskQueue
from arena.battle_plans import capture,revalidate,compact_payload
from test_realtime_runtime import Backend,unit,world
from test_opencode_bridge import reply,MODEL

class Server:
    def __init__(self,delay=.16,poll_delay=0):
        self.delay=delay;self.poll_delay=poll_delay;self.sessions=0;self.aborts=[];self.messages=[]
    def request(self,method,path,**kw):
        if path=='/session':self.sessions+=1;return {'id':'s'+str(self.sessions)}
        if path.endswith('/abort'):self.aborts.append(path);return True
        if path=='/session/status':time.sleep(self.poll_delay);return {}
        self.messages.append((path,kw['json']));time.sleep(self.delay)
        value=reply()
        if kw['json'].get('format'):
            value['info'].update(structured={'tasks':[]},finish='tool-calls');value['parts']=[]
        return value

class TransportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
    def planner(self,server):
        c=OpenCodePlanner(server,MODEL,0,self.tmp.name);c.phase='battle';c.soft_timeout=.05;c.timeout=.5
        self.addCleanup(c.close);return c
    def records(self,c):return [json.loads(x) for x in c.audit_path.read_text().splitlines()]
    def test_soft_deadline_keeps_request_alive_and_visible(self):
        server=Server(.25);c=self.planner(server)
        thread=threading.Thread(target=lambda:c.chat('contract','{}'));thread.start()
        end=time.monotonic()+1
        while c.request_status().get('state')!='slow' and time.monotonic()<end:time.sleep(.005)
        self.assertEqual(c.request_status()['state'],'slow');thread.join(2)
        self.assertEqual(c.calls,1);self.assertFalse(server.aborts)
        self.assertEqual(sum(r['record']=='response_slow' for r in self.records(c)),1)
    def test_hard_deadline_aborts_and_never_accepts_late_result(self):
        server=Server(.22);c=self.planner(server);c.timeout=.08
        with self.assertRaisesRegex(TimeoutError,'request aborted'):c.chat('s','{}')
        self.assertEqual(len(server.aborts),1);time.sleep(.25)
        self.assertEqual(c.calls,0);self.assertIsNone(c.last_raw)
        self.assertEqual(c.request_status()['state'],'failed')
    def test_completed_after_deadline_during_slow_poll_is_rejected(self):
        c=self.planner(Server(.08,poll_delay=.15));c.timeout=.06
        with self.assertRaises(TimeoutError):c.chat('s','{}')
        self.assertEqual(c.calls,0)
    def test_battle_has_fresh_sessions_and_developer_keeps_history(self):
        server=Server(.001);c=self.planner(server)
        c.chat('s','{}');c.chat('s','{}')
        self.assertNotEqual(server.messages[0][0],server.messages[1][0]);self.assertEqual(server.sessions,3)
        c.phase='developer';c.chat('s','repair 1');c.chat('s','repair 2')
        self.assertEqual(server.messages[2][0],server.messages[3][0]);self.assertEqual(server.sessions,3)
    def test_reset_failure_is_visible_no_old_history_fallback(self):
        server=Server(.001);c=self.planner(server)
        original=server.request
        def fail(method,path,**kw):
            if path=='/session':raise RuntimeError('fixture session failure')
            return original(method,path,**kw)
        server.request=fail
        with self.assertRaisesRegex(RuntimeError,'session failure'):c.chat('s','{}')
        self.assertFalse(server.messages)
        self.assertIn('battle_context_reset_failed',[r['record'] for r in self.records(c)])

class LatePlanTests(unittest.TestCase):
    def setUp(self):
        self.b=Backend();self.w=world([unit(1,18,is_building=True),unit(2,45),unit(3,21,is_building=True),unit(4,48)])
        self.q=TaskQueue(self.b,0,lambda x:None)
        self.before=capture(self.w,self.q);self.now=replace(self.w,game_loop=784)
    def check(self,tasks,w=None,**kw):return revalidate(dict(tasks=tasks,**kw),self.before,w or self.now,self.q,self.b.homes[0],35)
    def test_late_economy_and_stable_attack_allowed(self):
        self.assertEqual(self.check([{'skill':'train_worker'},{'skill':'train_unit'},{'skill':'attack_nearest'}]),[])
    def test_lost_producer_and_already_completed_production_rejected(self):
        for units in ([u for u in self.w.units if u.tag!=3],list(self.w.units)+[unit(5,48)]):
            self.assertTrue(self.check([{'skill':'train_unit'}],replace(self.now,units=tuple(units))))
    def test_supply_no_longer_needed_and_new_home_threat_rejected(self):
        self.assertTrue(self.check([{'skill':'supply_depot'}],replace(self.now,supply_cap=40)))
        self.assertTrue(self.check([{'skill':'barracks'}],replace(self.now,units=self.w.units+(unit(9,48,alliance=4),))))
    def test_disappeared_target_damaged_or_reserved_squad_rejected(self):
        enemy=unit(9,48,100,100,alliance=4)
        self.before=capture(replace(self.w,units=self.w.units+(enemy,)),self.q)
        self.assertTrue(self.check([{'skill':'attack_nearest'}]))
        self.before=capture(self.w,self.q)
        injured=tuple(replace(u,health=1) if u.tag==4 else u for u in self.w.units)
        self.assertTrue(self.check([{'skill':'move_out'}],replace(self.now,units=injured)))
    def test_surrender_new_workflow_missing_observation_and_absolute_limit(self):
        self.assertTrue(self.check([{'skill':'surrender','args':{'reason':'lost'}}]))
        self.assertTrue(self.check([],skills=[{'name':'new'}]))
        self.assertTrue(revalidate({'tasks':[]},None,self.now,self.q,self.b.homes[0],35))
        self.assertTrue(revalidate({'tasks':[]},self.before,self.now,self.q,self.b.homes[0],71))
    def test_python_version_change_or_repair_block_rejects_late_dispatch(self):
        from types import SimpleNamespace
        self.q.python_catalog=SimpleNamespace(entries={'tactic':{'sha256':'old','manifest':{'unit':'marine'}}})
        self.before=capture(self.w,self.q)
        plan=[{'skill':'tactic','args':{'count':1,'target':'enemy_start','retreat_target':'home','timeout_s':60,'params':{}}}]
        self.assertEqual(self.check(plan),[])
        self.q.python_catalog.entries['tactic']['sha256']='new'
        self.assertTrue(self.check(plan))
        self.q.python_catalog.entries['tactic']['sha256']='old';self.q.python_blocked.add('tactic')
        self.assertTrue(self.check(plan))
    def test_cancel_checks_original_task_is_still_active(self):
        from arena.tasks import Task
        self.q.tasks=[Task(7,'tactic')];self.before=capture(self.w,self.q)
        plan=[{'skill':'cancel_python_task','args':{'task_id':7}}]
        self.assertEqual(self.check(plan),[])
        self.q.tasks[0].status='failed';self.assertTrue(self.check(plan))
    def test_commander_accepts_checked_plan_and_atomically_rejects_invalid_mix(self):
        class Client:
            compact_battle=True;soft_timeout=22;timeout=60
            def chat(self,s,u):return '{"tasks":[]}'
        with tempfile.TemporaryDirectory() as d:
            self.b.initial=[self.w];c=Commander(self.b,0,Client(),opening=False,feedback_dir=d)
            try:
                c.request_observations[1]=capture(self.w,c.tasks);c.last_request=35
                c.outbox.put((0,{'tasks':[{'skill':'train_worker'}]},None,35,True,'fixture',1,[]))
                c.tick(self.now);self.assertEqual(c.decisions,1);self.assertEqual(c.late_plans_accepted,1)
                prior=len(c.tasks.tasks);c.request_observations[2]=capture(self.w,c.tasks)
                bad={'tasks':[{'skill':'barracks'},{'skill':'surrender','args':{'reason':'late'}}]}
                c.outbox.put((0,bad,None,35,True,'fixture',2,[]));c.tick(self.now)
                self.assertEqual(len(c.tasks.tasks),prior);self.assertEqual(c.plans_rejected,1)
                self.assertTrue(any(e['kind']=='plan_expired' and e['revalidation_issues'] for e in c.feedback.pending))
            finally:c.stop()

class ContextTests(unittest.TestCase):
    def test_paging_preserves_full_failure_and_drains_every_event(self):
        with tempfile.TemporaryDirectory() as d:
            j=FeedbackJournal(1,d)
            for n in range(15):j.add('routine',n,text=str(n))
            error=j.add('python_skill_failed',16,traceback='full traceback'*2000,context={'observed':list(range(50))})
            first=j.batch(max_events=8,max_chars=8000,prioritize_failures=True)
            j.acknowledge([e['id'] for e in first['events']],1)
            # Even an oversized failure is delivered promptly and intact beside the oldest event.
            self.assertIn(error,first['events']);self.assertTrue(first['oversized_event'])
            seen=[e['id'] for e in first['events']]
            while j.pending:
                page=j.batch(max_events=8,max_chars=8000,prioritize_failures=True)
                ids=[e['id'] for e in page['events']];seen+=ids;j.acknowledge(ids,2)
            self.assertEqual(sorted(seen),list(range(1,17)))
    def test_strategy_truncation_explicit_and_errors_untouched(self):
        payload={'prepared_strategy':'x'*1300,'feedback':{'events':[{'error':'full'}]},'recent_events':['duplicate']}
        value=compact_payload(payload)
        self.assertEqual(value['feedback'],payload['feedback']);self.assertNotIn('recent_events',value)
        self.assertTrue(value['prepared_strategy']['truncated']);self.assertEqual(value['prepared_strategy']['original_chars'],1300)

if __name__=='__main__':unittest.main()
