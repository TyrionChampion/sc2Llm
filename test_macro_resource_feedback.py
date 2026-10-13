"""Waiting reasons identify the actual shortage, including model-visible supply blocks."""
import unittest
from dataclasses import replace
import test_multirace as fixtures
from arena.tasks import TaskQueue

class ResourceFeedback(unittest.TestCase):
 def setup_queue(self):
  f=fixtures.MultiRace();f.setUp();b=f.backend('terran',[560]);q=TaskQueue(b,0,emit=lambda message:None);q.enqueue([dict(skill='train_unit',args=dict(unit='marine'))],0)
  return f,q
 def test_supply_block_with_two_thousand_minerals_does_not_report_money_shortage(self):
  f,q=self.setup_queue();w=replace(f.world([f.unit(1,21)]),minerals=2000,supply_used=30,supply_cap=30)
  self.assertEqual(q.tick(w,set()),[]);reason=q.tasks[0].reason
  self.assertIn('free supply required 1, available 0',reason);self.assertNotIn('minerals',reason);self.assertNotIn('gas',reason)
 def test_mineral_shortage_keeps_exact_cost_and_available_bank(self):
  f,q=self.setup_queue();w=replace(f.world([f.unit(1,21)]),minerals=30)
  self.assertEqual(q.tick(w,set()),[]);self.assertIn('minerals required 50, available 30',q.tasks[0].reason)
if __name__=='__main__':unittest.main()
