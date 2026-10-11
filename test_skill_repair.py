import json,tempfile,time,unittest,threading
from pathlib import Path
from copy import deepcopy
from unittest.mock import patch
from arena.skill_repair import SkillRepair
from arena.skill_lab import SkillLab,fixture_context
from arena.python_skills import PythonCatalog
from arena.commander import Commander,ScriptedPlanner
from test_realtime_runtime import Backend,unit,world
from test_skill_lab import SOURCE,MANIFEST,TEST
ARGS=dict(count=1,target='enemy_start',retreat_target='home',timeout_s=90,params={})
class RepairTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.lab=SkillLab(self.root/'lab');self.lab.setup()
  self.lab.write('skills/advance.py',SOURCE);self.lab.write('skills/advance.json',json.dumps(MANIFEST));self.lab.write('tests/test_advance.py',TEST)
  self.catalog=PythonCatalog(self.lab.root/'skills');self.old=self.catalog.entries['advance']['sha256']
  self.w=world([unit(101,48,ground_dps=10)]);self.backend=Backend();self.backend.initial=[self.w]
  self.dev=type('Developer',(),{'close':lambda s:None})()
  self.repair=SkillRepair(self.dev,self.lab,self.root/'repairs')
  self.c=Commander(self.backend,0,ScriptedPlanner(),opening=False,feedback_dir=self.root/'feedback',python_catalog=self.catalog,repair=self.repair)
 def tearDown(self):self.c.stop();self.repair.pool.shutdown(wait=True);self.tmp.cleanup()
 def failure(self):return dict(skill='advance',sha256=self.old,error='ValueError: recorded source defect',context=fixture_context(),execution_context=fixture_context(),state={})
 def test_background_install_keeps_active_snapshot_and_llm_must_redeploy(self):
  self.c.tasks.enqueue([dict(skill='advance',args=ARGS)],0);active=self.c.tasks.tasks[-1]
  gate=threading.Event();folder=self.root/'new';folder.mkdir()
  (folder/'advance.json').write_text(json.dumps(MANIFEST));(folder/'advance.py').write_text(SOURCE+'# repaired\n');new=PythonCatalog(folder)
  def develop(*a,**kw):gate.wait(2);return new,{'validation':{'scope':'fixture'}}
  with patch('arena.skill_repair.develop',develop):
   start=time.monotonic();self.c.record_feedback('python_skill_failed',**self.failure());self.assertLess(time.monotonic()-start,.05)
   self.assertIn('advance',self.c.tasks.python_blocked)
   self.c.tasks.enqueue([dict(skill='supply_depot')],0)
   with self.assertRaises(ValueError):self.c.tasks.enqueue([dict(skill='advance',args=ARGS)],0)
   gate.set();self.repair.future.result(timeout=3);self.repair.poll()
  self.assertEqual(self.catalog.entries['advance']['sha256'],new.entries['advance']['sha256'])
  self.assertEqual(active.program.entry['sha256'],self.old)
  self.assertEqual(len(self.c.tasks.tasks),2) # installing never auto-enqueues
  self.assertEqual(self.repair.public()['advance']['state'],'ready')
 def test_failed_repair_blocks_skill_but_not_macro(self):
  with patch('arena.skill_repair.develop',side_effect=ValueError('regression did not pass')):
   self.c.record_feedback('python_skill_failed',**self.failure())
   try:self.repair.future.result(timeout=3)
   except ValueError:pass
   self.repair.poll()
  self.assertEqual(self.catalog.entries['advance']['sha256'],self.old)
  self.assertEqual(self.repair.public()['advance']['state'],'failed')
  self.assertIn('advance',self.c.tasks.python_blocked)
  self.c.tasks.enqueue([dict(skill='train_worker')],0)
 def test_persistent_save_runs_in_background_and_failure_does_not_install(self):
  from unittest.mock import Mock
  folder=self.root/'new';folder.mkdir();(folder/'advance.json').write_text(json.dumps(MANIFEST));(folder/'advance.py').write_text(SOURCE+'# repaired\n');new=PythonCatalog(folder)
  gate=threading.Event();store=Mock()
  def publish(*a,**kw):gate.wait(2);raise OSError('persistent disk failure')
  store.publish.side_effect=publish;self.repair.library=store;self.repair.library_version='old-version';self.dev.model='provider/model'
  with patch('arena.skill_repair.develop',return_value=(new,{'frozen_directory':str(folder),'validation':{'scope':'fixture'}})):
   start=time.monotonic();self.c.record_feedback('python_skill_failed',**self.failure());self.assertLess(time.monotonic()-start,.05)
   self.c.tasks.enqueue([dict(skill='train_worker')],0);self.assertEqual(self.catalog.entries['advance']['sha256'],self.old)
   gate.set()
   try:self.repair.future.result(timeout=3)
   except OSError:pass
   self.repair.poll()
  self.assertEqual(self.repair.public()['advance']['state'],'failed');self.assertIn('persistent disk failure',self.repair.public()['advance']['error'])
  self.assertEqual(self.catalog.entries['advance']['sha256'],self.old)
  self.assertEqual(store.publish.call_args.kwargs['expected'],'old-version')

 def test_repair_is_published_then_a_new_process_restores_exact_source(self):
  from arena.persistent_skills import PersistentSkills
  import subprocess,sys
  model='provider/model';self.dev.model=model
  self.lab.run_tests();self.lab.validate();catalog,checks=self.lab.accept(self.root/'baseline')
  report=dict(status='prepared',model=model,strategy='fixture strategy',skills=catalog.public(),frozen_directory=str(self.root/'baseline'),**checks)
  store=PersistentSkills(self.root/'library');first=store.publish(model,self.lab,catalog,report)
  self.repair.library=store;self.repair.library_version=first['version']
  self.c.tasks.enqueue([dict(skill='advance',args=ARGS)],0);active=self.c.tasks.tasks[-1]
  def repaired(planner,lab,destination,*a,**kw):
   lab.write('skills/advance.py',SOURCE+'# repaired fixture source\n');lab.run_tests();lab.validate();new,checks=lab.accept(destination)
   return new,dict(status='prepared',model=model,strategy='fixture repair strategy',skills=new.public(),frozen_directory=str(destination),**checks)
  with patch('arena.skill_repair.develop',repaired):
   self.c.record_feedback('python_skill_failed',**self.failure());self.repair.future.result(timeout=10);self.repair.poll()
  state=self.repair.public()['advance'];self.assertEqual(state['state'],'ready');self.assertEqual(state['persistent']['version'],store.latest(model)['version'])
  self.assertEqual(active.program.entry['sha256'],self.old);self.assertNotEqual(self.catalog.entries['advance']['sha256'],self.old)
  code='from arena.persistent_skills import PersistentSkills; from arena.skill_lab import SkillLab; import sys; l=SkillLab(sys.argv[2]); l.setup(); c,r=PersistentSkills(sys.argv[1]).restore("provider/model",l,sys.argv[3]); print("RESTORED="+c.entries["advance"]["sha256"])'
  root=Path(__file__).resolve().parent
  result=subprocess.run([str(root/'run_sc2.sh'),sys.executable,'-c',code,str(store.root),str(self.root/'cold-lab'),str(self.root/'cold-accepted')],cwd=root,capture_output=True,text=True,timeout=15)
  self.assertEqual(result.returncode,0,result.stderr);self.assertIn('RESTORED='+state['new_sha256'],result.stdout)

 def test_mission_death_is_not_source_repair(self):
  d=self.failure();d['error']='ValueError: allocated unit lost; no replacement';self.c.record_feedback('python_skill_failed',**d)
  self.assertIsNone(self.repair.future);self.assertFalse(self.c.tasks.python_blocked)
if __name__=='__main__':unittest.main()
