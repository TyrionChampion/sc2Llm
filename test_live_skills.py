"""Synthetic integration checks. No provider calls and no native game is started."""
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from copy import deepcopy
from dataclasses import replace
from unittest.mock import patch
from types import SimpleNamespace
from concurrent.futures import Future

from arena.live_skills import (develop_live,LiveSkillDevelopment,queen_cases,verify_profile,
                              restore_live,IDENTITY,DEVELOPER_SYSTEM)
from arena.python_skills import PythonCatalog
from arena.knowledge import cached_tree
from arena.commander import Commander,ScriptedPlanner
from arena.battle_plans import output_schema
from test_realtime_runtime import Backend,unit,world

# Host-authored synthetic fixture, not a DeepSeek-generated or native-verified skill.
SOURCE = """def tick(ctx,state):
    actions=[]
    state=dict(state)
    for q in ctx['units']:
        key=str(q['tag'])
        bases=[b for b in ctx['allies'] if b['type_id'] in (86,100,101)]
        if not bases:continue
        b=min(bases,key=lambda x:(x['pos'][0]-q['pos'][0])**2+(x['pos'][1]-q['pos'][1])**2)
        if 11 in b['buffs']:
            state.pop(key,None)
            continue
        if key in state and ctx['game_s']-state[key]<3:continue
        spells=ctx['available_spells'].get(key,[])
        if q['energy']>=25 and any(s['name']=='effect_inject_larva' for s in spells):
            actions.append(dict(action='cast',unit_tags=[q['tag']],ability='effect_inject_larva',target=b['tag']))
            state[key]=ctx['game_s']
    return dict(actions=actions,state=state,status='running',reason='等待或注卵')
"""
MANIFEST=dict(name='queen_cycle',description='测试专用注卵循环',file='queen_cycle.py',unit='queen',actions=['cast'],parameters={})
TEST = """import unittest,json,queen_cycle
class Checks(unittest.TestCase):
 def test_cycle(self):
  with open('/lab/fixtures/verification.json') as f: c=json.load(f)
  ready=queen_cycle.tick(c['ready'],{})
  self.assertEqual(ready['actions'][0]['target'],301)
  for key in ('no_energy','already_injected','no_base','no_queen'):
   self.assertEqual(queen_cycle.tick(c[key],{})['actions'],[])
  self.assertEqual(queen_cycle.tick(c['ready'],ready['state'])['actions'],[])
"""
REQUEST=dict(name='queen_cycle',unit='queen',goal='女王循环注卵',verification='queen_inject')
def answer(source=SOURCE,test=TEST):
 return json.dumps(dict(files={'skills/queen_cycle.py':source,'skills/queen_cycle.json':json.dumps(MANIFEST),
                              'tests/test_queen_cycle.py':test},strategy='按需派遣女王循环注卵'),ensure_ascii=False)
class Developer:
 def __init__(self,replies):self.replies=list(replies);self.prompts=[];self.last_usage=None
 def chat(self,system,user):
  self.prompts.append(json.loads(user))
  if not self.replies:raise AssertionError('unexpected extra developer call')
  result=self.replies.pop(0)
  if isinstance(result,Exception):raise result
  return result
def job(store=None):
 return dict(request=deepcopy(REQUEST),race='zerg',old_entry=None,failure=None,
             observation=dict(world={'race':'zerg'},local_context=queen_cases()['ready']),
             map_size=[200,200],store_root=str(store) if store else None,persistent_before=None)

class AuthoringTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
 def tearDown(self):self.tmp.cleanup()
 def develop(self,developer,store=None):
  folder=self.root/'job';folder.mkdir()
  return develop_live(job(store),folder,developer)
 def test_repair_round_receives_exact_failure_then_publishes_and_restores(self):
  bad=SOURCE.replace("target=b['tag']","target=99999")
  dev=Developer([answer(bad),answer()])
  report=self.develop(dev,self.root/'store')
  self.assertEqual(len(dev.prompts),2)
  self.assertIn('99999 != 301',dev.prompts[1]['previous_diagnostics'])
  self.assertIsNotNone(dev.prompts[1]['previous_reply'])
  catalog,restored=restore_live(self.root/'store','zerg',self.root/'restored')
  self.assertEqual(catalog.public(),report['skills']);self.assertEqual(restored[0]['state'],'loaded')
  empty,other=restore_live(self.root/'store','terran',self.root/'terran')
  self.assertIsNone(empty);self.assertEqual(other,[])
 def test_noop_cannot_pass_inject_acceptance(self):
  noop="def tick(ctx,state):\n return dict(actions=[],state={},status='running',reason='noop')\n"
  trivial="import unittest\nclass T(unittest.TestCase):\n def test_x(self):self.assertTrue(True)\n"
  dev=Developer([answer(noop,trivial),answer(noop,trivial)])
  with self.assertRaisesRegex(ValueError,'ready case must inject'):self.develop(dev)
  self.assertEqual(len(dev.prompts),2)
  self.assertFalse((self.root/'job/accepted').exists())
 def test_network_error_not_retried(self):
  dev=Developer([TimeoutError('provider deadline')])
  with self.assertRaisesRegex(TimeoutError,'provider deadline'):self.develop(dev)
  self.assertEqual(len(dev.prompts),1)
 def test_path_escape_rejected(self):
  data=json.loads(answer());data['files']['../secret']='oops'
  dev=Developer([json.dumps(data)]*2)
  with self.assertRaisesRegex(ValueError,'exactly'):self.develop(dev)
  self.assertFalse((self.root/'secret').exists())
 def test_repair_replays_actual_failure(self):
  j=job();j['old_entry']={**MANIFEST,'manifest':MANIFEST,'source':SOURCE+'# old','sha256':'older'}
  j['failure']=dict(skill='queen_cycle',context=queen_cases()['ready'],state={},map_size=[200,200])
  folder=self.root/'job';folder.mkdir()
  report=develop_live(j,folder,Developer([answer()]))
  self.assertIn('failure_replay',report['validation'])
 def test_developer_high_and_main_unchanged_no_network(self):
  from arena.deepseek_planner import DeepSeekFlashPlanner
  d=DeepSeekFlashPlanner(developer=True);main=DeepSeekFlashPlanner();body=[]
  response=dict(model=d.model,choices=[dict(finish_reason='stop',message=dict(content='{}'))])
  def post(url,payload,headers):body.append(payload);return response
  with patch.object(d,'_post',side_effect=post):self.assertEqual(d._call_openai('test','test'),'{}')
  self.assertEqual(body[0]['reasoning_effort'],'high');self.assertEqual(d.timeout,90)
  self.assertEqual(main.reasoning_effort,'low');self.assertEqual(main.timeout,40)

class FakeProcess:
 def __init__(self):self.pid=9999999;self.returncode=None
 def poll(self):return self.returncode
 def wait(self,timeout=None):self.returncode=-9;return -9

class CoordinatorTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
  self.w=world([unit(101,126,energy=50),unit(301,86,x=32,y=30,is_building=True)])
  self.w=replace(self.w,race='zerg')
  self.backend=Backend();self.backend.initial=[self.w];self.backend.tech_tree=cached_tree()
  self.backend.available_abilities=lambda i,tags:{t:{251} for t in tags}
  self.d=LiveSkillDevelopment(self.root/'jobs')
  self.c=Commander(self.backend,0,SimpleNamespace(chat=lambda *a:'{"tasks":[],"reason":"fixture"}'),opening=False,feedback_dir=self.root/'feedback',development=self.d)
  self.c.tasks.race='zerg'
  self.proc=FakeProcess();self.spawn=patch('arena.live_skills.subprocess.Popen',return_value=self.proc);self.spawn.start()
 def tearDown(self):
  with patch('arena.live_skills.os.killpg'):self.c.stop()
  self.spawn.stop();self.tmp.cleanup()
 def start(self):self.d.request(REQUEST,self.w)
 def test_actual_developer_subprocess_with_explicit_mock_model(self):
  # Actual process/IPC/isolation; only the model response is replaced by the host fixture.
  import subprocess
  self.spawn.stop()
  real_popen=subprocess.Popen
  bootstrap="from arena.deepseek_planner import DeepSeekFlashPlanner;from test_live_skills import answer;DeepSeekFlashPlanner.chat=lambda *a:answer();from arena.live_skills import main;main()"
  def launch(argv,**kwargs):
   return real_popen([argv[0],'-c',bootstrap,'--job',argv[-1]],**kwargs)
  try:
   with patch('arena.live_skills.subprocess.Popen',side_effect=launch):
    start=time.monotonic();self.start()
    self.assertLess(time.monotonic()-start,1)
   deadline=time.monotonic()+20
   while self.d.process is not None and time.monotonic()<deadline:
    self.d.poll();time.sleep(.01)
   self.assertEqual(self.d.status['queen_cycle']['state'],'ready',self.d.status)
  finally:self.spawn.start()
 def test_source_failure_starts_repair_with_source_and_observations(self):
  folder=self.root/'original';folder.mkdir()
  (folder/'queen_cycle.py').write_text(SOURCE)
  (folder/'queen_cycle.json').write_text(json.dumps(MANIFEST))
  self.c.tasks.python_catalog=PythonCatalog(folder)
  self.c.ingest_feedback(self.w)
  old=self.c.tasks.python_catalog.entries['queen_cycle']
  self.d.status['queen_cycle']=dict(state='ready',verification='queen_inject')
  details=dict(skill='queen_cycle',sha256=old['sha256'],error='ValueError: test source defect',
    context=queen_cases()['ready'],execution_context=queen_cases()['ready'],state={})
  self.c.record_feedback('python_skill_failed',**details)
  j=json.loads((self.d.current['folder']/'job.json').read_text())
  self.assertEqual(j['old_entry']['source'],SOURCE)
  self.assertEqual(j['failure']['error'],details['error'])
  self.assertEqual(j['request']['verification'],'queen_inject')
  self.assertIn('queen_cycle',self.c.tasks.python_blocked)
 def test_development_events_visible_in_chinese_viewer(self):
  from llm_output_window import Feed
  feed=Feed()
  for kind in ('python_development_started','python_development_ready','python_development_failed'):
   feed.consume(dict(record='feedback_event',event=dict(kind=kind,skill='queen_cycle',error='explicit failure')))
  self.assertEqual(len(feed.entries),3)
  self.assertTrue(feed.entries[-1]['error'])
  self.assertIn('等待模型派遣',feed.entries[1]['status'])
 def test_schema_only_when_enabled(self):
  self.assertEqual(len(output_schema({})['anyOf']),2)
  s=output_schema({'live_skill_development':{'enabled':True}})
  self.assertIn('develop_skill',s['anyOf'][2]['properties'])
 def test_background_install_then_real_task_dispatch(self):
  self.start();self.assertIsNone(self.c.tasks.python_catalog)
  self.assertEqual(self.d.public()['jobs']['queen_cycle']['state'],'developing')
  # Cancel Popen patch while running the actual isolated lab subprocesses.
  self.spawn.stop()
  folder=self.d.current['folder'];j=json.loads((folder/'job.json').read_text())
  try:report=develop_live(j,folder,Developer([answer()]))
  finally:self.spawn.start()
  (folder/'result.json').write_text(json.dumps(dict(passed=True,report=report)))
  self.proc.returncode=0;self.d.poll()
  self.assertEqual(self.d.status['queen_cycle']['state'],'ready')
  self.assertFalse(self.c.tasks.tasks)
  args=dict(count=1,target='home',retreat_target='home',timeout_s=90,params={})
  self.c.tasks.enqueue([dict(skill='queen_cycle',args=args)],self.w.seconds)
  self.assertEqual(self.c.tasks.tasks[-1].skill,'queen_cycle')
  self.assertTrue(self.c.control_policy['python_tactics_enabled'])
 def test_failure_visible_and_no_install(self):
  self.start();folder=self.d.current['folder']
  (folder/'result.json').write_text(json.dumps(dict(passed=False,error='specific test failure')))
  self.proc.returncode=1;self.d.poll()
  self.assertIsNone(self.c.tasks.python_catalog)
  self.assertIn('specific test failure',self.d.status['queen_cycle']['error'])
 def test_parallel_budget_and_race_checks(self):
  wrong={**REQUEST,'unit':'marine'}
  with self.assertRaises(ValueError):self.d.validate(wrong)
  self.start()
  with self.assertRaisesRegex(ValueError,'already running'):self.d.validate(REQUEST)
  self.proc.returncode=1;self.d.poll();self.d.count=3
  with self.assertRaisesRegex(ValueError,'budget exhausted'):self.d.validate(REQUEST)
 def test_close_terminates_developer_and_prevents_install(self):
  self.start()
  with patch('arena.live_skills.os.killpg') as kill:self.d.close();kill.assert_called_once()
  self.d.poll();self.assertIsNone(self.c.tasks.python_catalog)
  self.assertEqual(self.d.status['queen_cycle']['state'],'cancelled')
 def test_outer_deadline_terminates_no_blocking_network(self):
  self.start();self.d.started-=241
  with patch('arena.live_skills.os.killpg') as kill:self.d.poll();kill.assert_called_once()
  self.assertIn('240 wall seconds',self.d.status['queen_cycle']['error'])
 def test_mixed_plan_rejected_atomically(self):
  self.c.outbox.put((self.w.seconds,dict(tasks=[dict(skill='no_op')],develop_skill=REQUEST,reason='test'),None,0,True,'{}',1,[]))
  self.c.tick(self.w)
  self.assertIsNone(self.d.process)
  self.assertFalse(self.c.tasks.tasks)
  self.assertEqual(self.c.errors,1)

if __name__=='__main__':unittest.main()
