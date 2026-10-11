"""Fixture and actual isolated-worker contracts; NOT evidence of native game behavior."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from arena.python_skills import PythonCatalog, PythonSkillRun, IsolatedWorker
from arena.tasks import TaskQueue, ALLOWED, PlanValidationError
from arena.commander import Commander
from test_realtime_runtime import Backend, unit, world

ARGS=dict(count=1,target="enemy_start",retreat_target="home",timeout_s=30,params={})
def result(actions=None,**kw):
    return dict(actions=actions or [],state={},status="running",reason="fixture",**kw)
def entry(code):
    return dict(source=code.encode(),sha256=hashlib.sha256(code.encode()).hexdigest(),
        manifest=dict(name="fixture",unit="marine",actions=["move","attack_move","attack_unit","stim","siege","gather"],parameters={}))
def get(worker,timeout=4):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        value=worker.poll()
        if value is not None:return value
        time.sleep(.002)
    raise AssertionError("worker did not finish")
class WorkerTests(unittest.TestCase):
    def test_real_python_state_print_and_isolation(self):
        worker=IsolatedWorker(entry("""import math, os
print('import diagnostic')
def tick(ctx,state):
    return {'root':math.sqrt(81),'state':state,'secret':os.getenv('DEEPSEEK_API_KEY'),
      'project_visible':os.path.exists('/home/tyrion/Projects/wolf/sc2-arena/.env'),
      'net':os.readlink('/proc/self/ns/net')}
"""))
        try:
            start=time.monotonic();worker.submit({},dict(n=7))
            self.assertLess(time.monotonic()-start,.03)
            value=get(worker);self.assertEqual(value['result']['root'],9)
            self.assertEqual(value['result']['state'],{'n':7});self.assertIsNone(value['result']['secret'])
            self.assertFalse(value['result']['project_visible'])
            self.assertNotEqual(value['result']['net'],os.readlink('/proc/self/ns/net'))
            self.assertIn('import diagnostic',value['diagnostics'])
        finally:worker.close()
    def test_exception_includes_source_traceback(self):
        worker=IsolatedWorker(entry("def tick(ctx,state):\n    raise ValueError('specific failure')"))
        try:
            worker.submit({},{});value=get(worker)
            self.assertIn('ValueError: specific failure',value['error'])
            self.assertIn('/skill/tactic.py',value['traceback'])
        finally:worker.close()
    def test_hanging_tick_import_and_output_flood_are_killed(self):
        for code,error in [("def tick(ctx,state):\n    while True: pass",TimeoutError),
                           ("while True: pass",TimeoutError),
                           ("def tick(ctx,state):\n    print('x'*70000)\n    return {}",ValueError)]:
            with self.subTest(code=code):
                worker=IsolatedWorker(entry(code),startup_s=.15)
                try:
                    worker.submit({},{})
                    with self.assertRaises(error):get(worker)
                    self.assertIsNotNone(worker.proc.poll())
                finally:worker.close()
    def test_abrupt_exit_preserves_stderr(self):
        worker=IsolatedWorker(entry("import os,sys\ndef tick(ctx,state):\n    sys.stderr.write('specific crash clue\\n');sys.stderr.flush();os._exit(9)"))
        try:
            worker.submit({},{})
            with self.assertRaisesRegex(RuntimeError,'specific crash clue'):get(worker)
        finally:worker.close()
    def test_close_during_spawn_and_execution(self):
        for delay in (0,.02):
            worker=IsolatedWorker(entry('def tick(ctx,state):\n    while True: pass'))
            worker.submit({},{});time.sleep(delay);worker.close()
            worker.pool.shutdown(wait=True)
            if worker.proc:self.assertIsNotNone(worker.proc.poll())

class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();root=Path(self.temp.name)
        self.root=root
        self.write('def tick(ctx,state):\n    return '+repr(result([dict(action='move',unit_tags=[2],target='objective')])))
        self.catalog=PythonCatalog(root,reserved=ALLOWED)
        self.q=TaskQueue(Backend(),0,lambda _:None,python_catalog=self.catalog)
        self.events=[];self.q.feedback=lambda kind,**kw:self.events.append(dict(kind=kind,**kw))
        self.w=world([unit(2,48,ground_dps=10),unit(3,48,ground_dps=10)])
    def write(self,code,name='fixture'):
        (self.root/(name+'.py')).write_text(code)
        (self.root/(name+'.json')).write_text(json.dumps(dict(name=name,file=name+'.py',description='test fixture',unit='marine',actions=['move','attack_move','attack_unit','stim'],parameters={})))
    def tearDown(self):self.q.close();self.temp.cleanup()
    def start(self):
        self.q.enqueue([dict(skill='fixture',args=deepcopy(ARGS))],0)
        self.q.tick(self.w,set());return self.q.tasks[-1]
    def wait_output(self):
        end=time.monotonic()+3
        while time.monotonic()<end:
            output=self.q.tick(self.w,set())
            if output:return output
            time.sleep(.003)
        self.fail('no task output')
    def test_atomic_invalid_invocation_and_reserved_names(self):
        with self.assertRaises(PlanValidationError):self.q.enqueue([dict(skill='train_worker'),dict(skill='fixture',args={})],0)
        self.assertEqual(self.q.tasks,[])
        self.write('pass','surrender')
        with self.assertRaises(ValueError):PythonCatalog(self.root,reserved=ALLOWED)
    def test_source_frozen_and_ownership_kept(self):
        t=self.start();(self.root/'fixture.py').write_text('raise RuntimeError("changed after loading")')
        output=self.wait_output();self.assertEqual(list(output[0].action_raw.unit_command.unit_tags),[2])
        self.assertEqual(self.q.protected(),{2});self.assertEqual(t.status,'running')
        self.q.enqueue([dict(skill='attack_nearest')],0)
        output=self.q.tick(self.w,set())
        self.assertEqual(list(output[0].action_raw.unit_command.unit_tags),[3])
    def test_unknown_unit_target_macro_nan_and_atomic_actions_rejected(self):
        t=self.start();p=t.program
        good=dict(action='move',unit_tags=[2],target='objective')
        bad=[dict(action='move',unit_tags=[3],target='objective'),
             dict(action='attack_unit',unit_tags=[2],target=99),
             dict(action='train_worker',unit_tags=[2]),
             dict(action='move',unit_tags=[2],target=[float('nan'),2]),
             dict(action='move',unit_tags=[2],target=[190,190]),
             dict(action='stim',unit_tags=[2])]
        for action in bad:
            with self.subTest(action=action),self.assertRaises(ValueError):p.validate_result(result([action]),self.w,self.q)
        with self.assertRaises(ValueError):p.validate_result(result([good,good]),self.w,self.q)
    def test_cancel_and_reassign_atomic(self):
        old=self.start();self.wait_output()
        self.q.enqueue([dict(skill='cancel_python_task',args=dict(task_id=old.id)),dict(skill='fixture',args=deepcopy(ARGS))],.1)
        self.assertEqual(old.status,'cancelled');self.assertTrue(old.program.worker.closed)
        out=self.q.tick(self.w,set());self.assertEqual(out[0].action_raw.unit_command.ability_id,4)
        self.assertEqual(self.q.tasks[-1].program.tags,{2})
        with self.assertRaises(PlanValidationError):self.q.enqueue([dict(skill='cancel_python_task',args=dict(task_id=old.id)),dict(skill='train_worker')],.2)
        self.assertEqual(len(self.q.tasks),2)
    def test_unit_loss_deadline_and_engine_rejection_are_visible(self):
        for mode in ('lost','deadline','engine'):
            with self.subTest(mode=mode):
                q=TaskQueue(Backend(),0,lambda _:None,python_catalog=self.catalog)
                try:
                    q.enqueue([dict(skill='fixture',args=deepcopy(ARGS))],0);q.tick(self.w,set())
                    w={'lost':world([]),'deadline':world(self.w.units,31),'engine':replace(self.w,errors=((2,3794,1),))}[mode]
                    q.tick(w,set());self.assertEqual(q.tasks[0].status,'failed')
                    self.assertTrue(q.tasks[0].program.worker.closed)
                finally:q.close()
    def test_stale_response_never_issues_movement(self):
        t=self.start();get(t.program.worker)
        # Inject a completed response to exercise host age gate independently of process speed.
        class Done:
            future=None
            def poll(self):return dict(result=result([dict(action='move',unit_tags=[2],target='objective')]),elapsed_ms=1)
            def close(self):pass
        t.program.worker.close();t.program.worker=Done()
        output=self.q.tick(world(self.w.units,1),set())
        self.assertEqual(t.status,'failed');self.assertIn('stale',t.reason)
        self.assertEqual([a.action_raw.unit_command.ability_id for a in output],[4])
    def test_partial_or_distant_enemy_observation_not_exposed(self):
        t=self.start();w=world([*self.w.units,unit(9,48,190,190,4)])
        self.assertEqual(t.program.context(w,self.q)['enemies'],[])
    def test_two_player_workers_and_state_are_independent(self):
        q2=TaskQueue(Backend(),0,lambda _:None,python_catalog=self.catalog)
        try:
            t1=self.start()
            q2.enqueue([dict(skill='fixture',args=deepcopy(ARGS))],0)
            q2.tick(self.w,set());t2=q2.tasks[0]
            self.assertIsNot(t1.program.worker,t2.program.worker)
            self.assertIsNot(t1.program.state,t2.program.state)
            t1.program.state['private']=1;self.assertNotIn('private',t2.program.state)
            self.q.close();self.assertFalse(t2.program.worker.closed)
        finally:q2.close()
    def test_stim_engine_query_required_after_tech_checks(self):
        t=self.start();p=t.program
        self.q.backend.available_abilities=lambda index,tags:{tag:{380} for tag in tags}
        w=replace(self.w,upgrades=(15,))
        action=result([dict(action='stim',unit_tags=[2])])
        self.assertEqual(p.validate_result(action,w,self.q)[0][1],3675)
        self.q.backend.available_abilities=lambda index,tags:{}
        with self.assertRaisesRegex(ValueError,'engine ability query'):p.validate_result(action,w,self.q)
    def test_own_script_error_visible_as_failed_not_completed(self):
        t=self.start();get(t.program.worker);t.program.worker.close()
        class Failed:
            def poll(self):return dict(error='ValueError: tactical mistake',traceback='tactic.py line 8')
            def close(self):pass
        t.program.worker=Failed()
        self.q.tick(self.w,set())
        self.assertEqual(t.status,'failed')
        event=next(e for e in self.events if e['kind']=='python_skill_failed')
        self.assertIn('tactical mistake',event['error']);self.assertIn('line 8',event['traceback'])
    def test_actual_commander_planner_path_exposes_catalog(self):
        class Planner:
            prompts=[]
            def chat(self,system,user):
                self.prompts.append(json.loads(user));return json.dumps(dict(tasks=[dict(skill='fixture',args=ARGS)]))
        b=Backend();b.initial=[self.w];client=Planner()
        with tempfile.TemporaryDirectory() as logs:
            c=Commander(b,0,client,opening=False,python_catalog=self.catalog,feedback_dir=logs,interval=999)
            try:
                c.tick(self.w);end=time.monotonic()+2
                while c.outbox.empty() and time.monotonic()<end:time.sleep(.002)
                c.tick(self.w)
                self.assertEqual(c.decisions,1);self.assertEqual(client.prompts[0]['python_skill_library'][0]['name'],'fixture')
                self.assertFalse(c.control_policy['combat_reflex_enabled'])
            finally:c.stop()

if __name__=='__main__':unittest.main()
