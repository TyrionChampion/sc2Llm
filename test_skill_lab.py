import json,tempfile,unittest,time
from pathlib import Path
from copy import deepcopy
from arena.skill_lab import SkillLab,fixture_context,replay_check
from arena.python_skills import PythonCatalog
SOURCE="def tick(ctx,state):\n    return dict(status='running',state=state,reason='move',actions=[dict(action='attack_move',unit_tags=[u['tag'] for u in ctx['units']],target='objective')])\n"
MANIFEST=dict(name='advance',description='fixture advance',file='advance.py',unit='marine',actions=['attack_move'],parameters={})
TEST="import unittest,advance\nclass Checks(unittest.TestCase):\n def test_move(self):\n  r=advance.tick({'units':[{'tag':101}]},{})\n  self.assertEqual(r['actions'][0]['unit_tags'],[101])\n"
class LabTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.lab=SkillLab(Path(self.tmp.name)/'lab');self.lab.setup()
  self.lab.write('skills/advance.py',SOURCE);self.lab.write('skills/advance.json',json.dumps(MANIFEST));self.lab.write('tests/test_advance.py',TEST)
 def tearDown(self):self.tmp.cleanup()
 def test_self_tests_host_validation_and_stale_receipts(self):
  self.assertTrue(self.lab.run_tests()['passed']);self.assertTrue(self.lab.validate()['passed'])
  c,r=self.lab.accept(Path(self.tmp.name)/'frozen');self.assertIn('advance',c.entries)
  self.lab.write('skills/advance.py',SOURCE+'# edit\n')
  with self.assertRaisesRegex(ValueError,'final edit'):self.lab.accept(Path(self.tmp.name)/'bad')
 def test_foreign_race_selector_is_rejected_before_isolated_execution(self):
  self.lab.race='protoss'
  report=self.lab.validate()
  self.assertFalse(report['passed']);self.assertIn('cannot allocate units for assigned race protoss',report['error'])
 def test_developer_prose_summary_explicitly_normalized(self):
  from arena.lab_prepare import ready_reply
  records=[];planner=type('P',(),{'index':0,'audit':lambda self,r:records.append(r)})()
  self.assertTrue(ready_reply('Tests pass. '+json.dumps(dict(ready=True,strategy='advance')),planner)['ready'])
  self.assertEqual(records[0]['record'],'developer_summary_format_normalized')
  with self.assertRaises(ValueError):ready_reply('{"ready":true,"strategy":"a"} {"ready":true,"strategy":"b"}',planner)
 def test_path_escape_and_symlink(self):
  for p in ('../secret','/etc/passwd','skills/../../x','fixtures/basic.json','skills/.env'):
   with self.subTest(p=p),self.assertRaises(ValueError):self.lab.write(p,'bad')
  (self.lab.root/'skills/link.py').symlink_to('/etc/passwd')
  with self.assertRaises(ValueError):self.lab.read('skills/link.py')
 def test_self_tests_isolated_and_failure_traceback(self):
  code="import unittest,os,socket\nclass Isolation(unittest.TestCase):\n def test_isolated(self):\n  self.assertFalse(os.path.exists(%r))\n  self.assertIsNone(os.getenv('DEEPSEEK_API_KEY'))\n  with self.assertRaises(OSError): socket.create_connection(('1.1.1.1',443),timeout=.1)\n" % str(Path(__file__).resolve().parent/'.env')
  self.lab.write('tests/test_isolation.py',code);self.assertTrue(self.lab.run_tests()['passed'])
  self.lab.write('tests/test_broken.py','raise ValueError("specific lab failure")')
  r=self.lab.run_tests();self.assertFalse(r['passed']);self.assertIn('specific lab failure',r['output'])
 def test_hang_is_bounded(self):
  self.lab.write('tests/test_hang.py','while True: pass')
  start=time.monotonic();r=self.lab.run_tests();self.assertFalse(r['passed']);self.assertLess(time.monotonic()-start,10)
 def test_empty_tests_not_accepted(self):
  (self.lab.root/'tests/test_advance.py').unlink();self.assertFalse(self.lab.run_tests()['passed'])
 def test_exact_recorded_target_disappearance_rejected(self):
  source="def tick(ctx,state):\n return dict(status='running',state={},reason='attack',actions=[dict(action='attack_unit',unit_tags=[101],target=ctx['enemies'][0]['tag'])])"
  self.lab.write('skills/advance.py',source);m=deepcopy(MANIFEST);m['actions']=['attack_unit'];self.lab.write('skills/advance.json',json.dumps(m))
  before=fixture_context();after=deepcopy(before);after['enemies']=[];after['game_loop']+=2
  with self.assertRaisesRegex(ValueError,'not currently visible'):replay_check(PythonCatalog(self.lab.root/'skills').entries['advance'],dict(context=before,execution_context=after))
 def test_host_validation_catches_in_range_execution_race(self):
  weak="def tick(ctx,state):\n action=dict(action='attack_move',unit_tags=[u['tag'] for u in ctx['units']],target='objective')\n if len(ctx['units'])>=3 and ctx['enemies']: action=dict(action='attack_unit',unit_tags=[u['tag'] for u in ctx['units']],target=ctx['enemies'][0]['tag'])\n return dict(status='running',state={},reason='fixture',actions=[action])"
  self.lab.write('skills/advance.py',weak);m=deepcopy(MANIFEST);m['actions']+=['attack_unit'];self.lab.write('skills/advance.json',json.dumps(m))
  bad=self.lab.validate();self.assertFalse(bad['passed']);self.assertIn('not currently visible',bad['error'])
  self.lab.write('skills/advance.py',SOURCE)
  good=self.lab.validate();self.assertTrue(good['passed'])
  self.assertIn('in_range_target_disappeared_before_execution',[x['case'] for x in good['edge_cases']])
if __name__=='__main__':unittest.main()
