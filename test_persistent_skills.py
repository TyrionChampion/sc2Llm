import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from arena.persistent_skills import PersistentSkills
from arena.skill_lab import SkillLab
from test_skill_lab import SOURCE,MANIFEST,TEST

class PersistentTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  self.store=PersistentSkills(self.root/'library');self.model='provider/model';self.lab=SkillLab(self.root/'lab');self.lab.setup()
  self.lab.write('skills/advance.py',SOURCE);self.lab.write('skills/advance.json',json.dumps(MANIFEST));self.lab.write('tests/test_advance.py',TEST)
  (self.lab.root/'fixtures/custom.json').write_text('{"regression":"preserve me"}')
  self.number=0;self.catalog,self.report=self.accept()
 def accept(self):
  self.number+=1;self.assertTrue(self.lab.run_tests()['passed']);self.assertTrue(self.lab.validate()['passed'])
  catalog,checks=self.lab.accept(self.root/f'accepted{self.number}')
  return catalog,dict(status='prepared',model=self.model,strategy='中文战略',skills=catalog.public(),frozen_directory=str(self.root/f'accepted{self.number}'),**checks)
 def publish(self,expected=None):return self.store.publish(self.model,self.lab,self.catalog,self.report,expected=expected)
 def test_fresh_store_restores_complete_bundle_and_exact_model(self):
  first=self.publish();store=PersistentSkills(self.root/'library');lab=SkillLab(self.root/'next');lab.setup()
  catalog,report=store.restore(self.model,lab,self.root/'next-accepted')
  self.assertEqual(catalog.public(),self.catalog.public());self.assertEqual(report['persistent']['version'],first['version'])
  self.assertTrue((lab.root/'tests/test_advance.py').exists());self.assertIn('preserve me',(lab.root/'fixtures/custom.json').read_text())
  self.assertIsNone(store.latest('other-provider/model'))
  with self.assertRaises(OSError):store.version('other-provider/model',first['version'])
 def test_changed_draft_and_failed_report_never_replace_latest(self):
  first=self.publish();self.lab.write('skills/advance.py',SOURCE+'# untested edit\n')
  with self.assertRaisesRegex(ValueError,'Draft changed'):self.publish(first['version'])
  self.assertEqual(self.store.latest(self.model)['version'],first['version'])
  self.lab.write('skills/advance.py',SOURCE);self.report['validation']['passed']=False
  with self.assertRaisesRegex(ValueError,'Both self-tests'):self.publish(first['version'])
  self.assertEqual(self.store.latest(self.model)['version'],first['version'])
 def test_fixture_corruption_is_visible_and_no_old_version_fallback(self):
  first=self.publish();self.lab.write('skills/advance.py',SOURCE+'# tested edit\n');self.catalog,self.report=self.accept();second=self.publish(first['version'])
  (Path(second['path'])/'fixtures/custom.json').write_text('{}')
  with self.assertRaisesRegex(ValueError,'integrity mismatch'):self.store.latest(self.model)
  self.assertTrue(Path(first['path']).is_dir())
 def test_stale_concurrent_promotion_cannot_overwrite_newer_head(self):
  first=self.publish();self.lab.write('skills/advance.py',SOURCE+'# tested repair\n');self.catalog,self.report=self.accept();second=self.publish(first['version'])
  with self.assertRaisesRegex(ValueError,'changed concurrently'):self.publish(first['version'])
  self.assertEqual(self.store.latest(self.model)['version'],second['version'])
 def test_large_recorded_observation_is_preserved_without_truncation(self):
  data=json.dumps({'observation':'x'*300000}).encode();(self.lab.root/'fixtures/large.json').write_bytes(data)
  saved=self.publish();self.assertEqual((Path(saved['path'])/'fixtures/large.json').read_bytes(),data)
  self.assertEqual(self.store.latest(self.model)['version'],saved['version'])
 def test_current_host_validation_failure_blocks_restore(self):
  self.publish();lab=SkillLab(self.root/'next');lab.setup()
  with patch.object(lab,'validate',return_value={'passed':False,'error':'new host regression'}):
   with self.assertRaisesRegex(ValueError,'startup revalidation'):self.store.restore(self.model,lab,self.root/'next-accepted')
  self.assertFalse((self.root/'next-accepted').exists())
 def test_atomic_head_write_failure_keeps_previous_pointer(self):
  first=self.publish();self.lab.write('skills/advance.py',SOURCE+'# next\n');self.catalog,self.report=self.accept()
  with patch('arena.persistent_skills.durable_json',side_effect=OSError('disk unavailable')):
   with self.assertRaisesRegex(OSError,'disk unavailable'):self.publish(first['version'])
  self.assertEqual(self.store.latest(self.model)['version'],first['version'])
 def test_fixtures_are_replayed_on_snapshot_even_when_old_digest_unchanged(self):
  first=self.publish()
  self.lab.write('tests/test_fixture.py','import unittest,json\nclass C(unittest.TestCase):\n def test_value(self):\n  self.assertEqual(json.load(open("/lab/fixtures/custom.json"))["regression"],"preserve me")\n')
  self.catalog,self.report=self.accept()
  (self.lab.root/'fixtures/custom.json').write_text('{"regression":"changed after testing"}')
  with self.assertRaisesRegex(ValueError,'snapshot revalidation failed'):self.publish(first['version'])
  self.assertEqual(self.store.latest(self.model)['version'],first['version'])

if __name__=='__main__':unittest.main()
